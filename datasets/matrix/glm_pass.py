"""GLM-4.6 pass over LangGraph corpus, appended as 5th provider into matrix.jsonl.

Uses Z.ai coding-plan endpoint (Anthropic-compatible) since the user has a
GLM coding subscription, not pay-per-token credits on the bigmodel.cn endpoint.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path("/root/claude-code-agentic/darwin-mvp")
sys.path.insert(0, str(ROOT))

from darwin_harness import DIAGNOSE_PROMPT, _extract_fix  # noqa: E402

CORPUS = ROOT / "datasets/github-failures/langchain-ai-langgraph.jsonl"
MATRIX_FILE = ROOT / "datasets/matrix/matrix.jsonl"
MODEL = os.environ.get("GLM_MODEL", "glm-4.6")
KEY = os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")
ENDPOINT = "https://api.z.ai/api/coding/paas/v4/chat/completions"

if not KEY:
    raise SystemExit("ZHIPU_API_KEY not set")


def call_glm(prompt: str, timeout: int = 300) -> tuple[str | None, str | None]:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        msg = data.get("choices", [{}])[0].get("message", {})
        return msg.get("content", "") or "", None
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")[:300]
        return None, f"HTTPError {e.code}: {body_txt}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def build_inputs(bug: dict) -> tuple[str, str]:
    repro = bug.get("repro_code", "") or ""
    stack = bug.get("stack_trace", "") or bug.get("error_excerpt", "")
    source = repro if repro.strip() else "# (no reproducer in issue body)\n"
    return source, stack


def classify_error(stderr: str) -> str:
    for tag in (
        "KeyError", "ValueError", "TypeError", "AttributeError",
        "RuntimeError", "ImportError", "ModuleNotFoundError",
        "FileNotFoundError", "AssertionError", "IndexError",
        "ValidationError", "PermissionError", "ConnectionError",
    ):
        if tag in stderr:
            return tag
    return "Other"


def is_healed(patch: str | None, source: str) -> bool:
    if not patch:
        return False
    try:
        import ast
        ast.parse(patch)
    except SyntaxError:
        return False
    return len(patch.strip()) > 20


def main() -> None:
    bugs = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    print(f"loaded {len(bugs)} bugs, model={MODEL}", flush=True)

    with MATRIX_FILE.open("a") as out:
        for i, bug in enumerate(bugs):
            source, stderr = build_inputs(bug)
            bug_id = bug.get("id", f"row_{i}")
            err_class = classify_error(stderr)
            prompt = DIAGNOSE_PROMPT.format(source_code=source, stderr=stderr)

            t0 = time.time()
            text, err = call_glm(prompt)
            latency = int((time.time() - t0) * 1000)
            patch = _extract_fix(text) if text else None
            healed = is_healed(patch, source)

            row = {
                "bug_id": bug_id,
                "provider": "glm-4.6",
                "healed": healed,
                "patch_diff": (patch or "")[:8000],
                "patch_len": len(patch or ""),
                "latency_ms": latency,
                "error_class": err_class,
                "error_in_heal": err,
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(
                f"[GLM] {i:>3} {bug_id} healed={healed} "
                f"lat={latency}ms patch_len={row['patch_len']} err={err}",
                flush=True,
            )
            time.sleep(1)  # gentle pacing


if __name__ == "__main__":
    main()
