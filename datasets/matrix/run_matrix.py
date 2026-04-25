"""Darwin Multi-LLM Healing Matrix runner.

Runs each LangGraph bug through 4 providers (claude_cli/Opus, gemini, alibaba/qwen,
heuristic) and records a matrix row per (bug_id, provider).

Calls darwin_harness functions directly without modifying the harness; for the
'alibaba' column we hit dashscope-intl OpenAI-compatible endpoint with the same
DIAGNOSE_PROMPT (since the harness has no built-in alibaba provider).

Outputs: matrix.jsonl, plus partial-flush every 25 bugs.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path("/root/claude-code-agentic/darwin-mvp")
sys.path.insert(0, str(ROOT))

# Force the budget ledger to allow LLM calls (we are running a benchmark).
os.environ.setdefault("DARWIN_DISABLE", "0")
os.environ["DARWIN_USE_CLAUDE_CLI"] = "1"

from darwin_harness import (  # noqa: E402
    diagnose_via_anthropic,  # not used (paid)
    diagnose_via_gemini,
    diagnose_via_claude_cli,
    _heuristic_fix,
    _extract_fix,
    DIAGNOSE_PROMPT,
    _apply_prompt_prefix,
)

CORPUS = ROOT / "datasets/github-failures/langchain-ai-langgraph.jsonl"
OUT_DIR = ROOT / "datasets/matrix"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MATRIX_FILE = OUT_DIR / "matrix.jsonl"

# ALIBABA_CODING_API_KEY is the dashscope-intl key (qwen-coder-plus).
ALIBABA_KEY = os.environ.get("ALIBABA_CODING_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
ALIBABA_BASE = os.environ.get(
    "ALIBABA_CODING_BASE_URL",
    "https://coding-intl.dashscope.aliyuncs.com/v1",
)
ALIBABA_MODEL = os.environ.get("ALIBABA_MODEL", "qwen3-coder-plus")


def diagnose_via_alibaba(source_code: str, stderr: str) -> str | None:
    """Call qwen-coder-plus via the OpenAI-compatible dashscope endpoint.

    Mirrors the harness's prompt application + _extract_fix recovery so the
    output is parsed identically to the other providers.
    """
    if not ALIBABA_KEY:
        return None
    prompt = _apply_prompt_prefix(DIAGNOSE_PROMPT.format(source_code=source_code, stderr=stderr))
    body = json.dumps({
        "model": ALIBABA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4096,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{ALIBABA_BASE.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {ALIBABA_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["choices"][0]["message"]["content"]
    return _extract_fix(text)


def load_bugs(limit: int | None = None) -> list[dict]:
    bugs = []
    with CORPUS.open() as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            row = json.loads(line)
            bugs.append(row)
    return bugs


def build_inputs(bug: dict) -> tuple[str, str]:
    """Map issue row → (source_code, stderr) inputs for the harness."""
    source = bug.get("repro_code") or ""
    stderr_parts = []
    if bug.get("error_excerpt"):
        stderr_parts.append(bug["error_excerpt"])
    if bug.get("stack_trace"):
        stderr_parts.append(bug["stack_trace"])
    stderr = "\n".join(p for p in stderr_parts if p)
    # Trim huge bodies — keep prompts under control.
    return source[:6000], stderr[:6000]


def is_healed(patch: str | None, source: str) -> bool:
    """A 'heal' = harness returned a non-trivial fix proposal that differs from input."""
    if not patch:
        return False
    p = patch.strip()
    if len(p) < 5:
        return False
    if p == source.strip():
        return False
    return True


PROVIDERS = ["claude_cli", "gemini", "alibaba", "heuristic"]
DOWN: dict[str, str] = {}
CONSEC_FAIL: dict[str, int] = {p: 0 for p in PROVIDERS}


def call_provider(provider: str, source: str, stderr: str) -> tuple[str | None, str | None]:
    """Returns (patch, error_in_heal_str_or_None)."""
    try:
        if provider == "claude_cli":
            return diagnose_via_claude_cli(source, stderr), None
        if provider == "gemini":
            return diagnose_via_gemini(source, stderr), None
        if provider == "alibaba":
            return diagnose_via_alibaba(source, stderr), None
        if provider == "heuristic":
            return _heuristic_fix(source, stderr), None
    except urllib.error.HTTPError as e:
        return None, f"HTTPError {e.code}: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:200]}"
    return None, "unknown_provider"


def classify_error(stderr: str) -> str:
    """Crude error class for clustering."""
    for tag in ("KeyError", "ValueError", "TypeError", "AttributeError",
                "RuntimeError", "ImportError", "ModuleNotFoundError",
                "FileNotFoundError", "AssertionError", "IndexError",
                "ValidationError", "PermissionError", "ConnectionError"):
        if tag in stderr:
            return tag
    return "Other"


def append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def run(bug_indices: list[int], bugs: list[dict], phase: str) -> None:
    """Run a list of bug indices × all UP providers, append to MATRIX_FILE."""
    flush_buf: list[dict] = []
    flush_every = 1  # write per-bug to survive crashes
    n_done = 0

    for i in bug_indices:
        bug = bugs[i]
        source, stderr = build_inputs(bug)
        bug_id = bug.get("id", f"row_{i}")
        err_class = classify_error(stderr)

        for provider in PROVIDERS:
            if provider in DOWN:
                # Skip — column marked down.
                continue
            t0 = time.time()
            patch, err = call_provider(provider, source, stderr)
            latency = int((time.time() - t0) * 1000)
            healed = is_healed(patch, source)

            if err is not None:
                CONSEC_FAIL[provider] += 1
                if CONSEC_FAIL[provider] >= 3 and provider not in DOWN:
                    DOWN[provider] = f"3 consecutive failures, last: {err}"
                    print(f"[DOWN] {provider} marked DOWN: {err}", flush=True)
            else:
                CONSEC_FAIL[provider] = 0

            row = {
                "bug_id": bug_id,
                "provider": provider,
                "healed": healed,
                "patch_diff": (patch or "")[:8000],
                "patch_len": len(patch or ""),
                "latency_ms": latency,
                "error_class": err_class,
                "error_in_heal": err,
            }
            flush_buf.append(row)
            print(
                f"[{phase}] {i:>3} {bug_id} {provider:>10} "
                f"healed={healed} lat={latency}ms err={err}",
                flush=True,
            )
            # Light pacing for the CC subscription path.
            if provider == "claude_cli":
                time.sleep(2)

        n_done += 1
        if n_done % flush_every == 0:
            append_jsonl(MATRIX_FILE, flush_buf)
            flush_buf.clear()

    if flush_buf:
        append_jsonl(MATRIX_FILE, flush_buf)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    bugs = load_bugs()
    print(f"loaded {len(bugs)} bugs from {CORPUS}", flush=True)

    if mode == "phase1":
        # Phase 1: first 10 sanity rows.
        if MATRIX_FILE.exists():
            MATRIX_FILE.unlink()
        run(list(range(min(10, len(bugs)))), bugs, "P1")
    elif mode == "phase2":
        # Phase 2: remaining rows after first 10 (already done in P1).
        run(list(range(10, len(bugs))), bugs, "P2")
    elif mode == "all":
        if MATRIX_FILE.exists():
            MATRIX_FILE.unlink()
        run(list(range(len(bugs))), bugs, "ALL")
    else:
        raise SystemExit(f"unknown mode: {mode}")

    print(f"DOWN providers: {DOWN}", flush=True)
    print(f"matrix file: {MATRIX_FILE}", flush=True)


if __name__ == "__main__":
    main()
