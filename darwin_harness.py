#!/usr/bin/env python3
"""
Darwin Harness — fail → diagnose → fix → validate → verify → learn.

Day 1 upgrades (Apr 21 2026):
  - Runs inside a tmpdir; never mutates tracked agent.py/config.yaml.
  - validate_fix() AST gate rejects poisoned patches before broadcast.
  - Scene 6 demonstrates the safety gate live.
  - finally/SIGINT cleanup — Ctrl-C leaves the repo clean.

Usage:
    python3 darwin_harness.py              # Full demo
    python3 darwin_harness.py --break-only # Just switch config to v2 (in tmpdir)
    python3 darwin_harness.py --fix-only   # Diagnose + fix once
"""

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import blackboard

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    from google import genai as _genai  # modern SDK
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

try:
    import yaml  # noqa: F401
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

BASE_DIR = Path(__file__).parent
PRISTINE_DIR = BASE_DIR / ".pristine"
FIXES_DIR = blackboard.FIXES_DIR
MAX_FIX_ATTEMPTS = 3

# Populated by init_run_env() — all per-run file ops target these.
RUN_DIR: Path | None = None
AGENT_FILE: Path | None = None
CONFIG_FILE: Path | None = None


# ─── Terminal colors ───────────────────────────────────────────────
class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def banner(msg: str, color: str = C.CYAN) -> None:
    width = 60
    print(f"\n{color}{C.BOLD}{'═' * width}")
    print(f"  {msg}")
    print(f"{'═' * width}{C.RESET}\n")


def step(msg: str) -> None:
    print(f"{C.YELLOW}▸{C.RESET} {msg}")


def success(msg: str) -> None:
    print(f"{C.GREEN}✓{C.RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{C.RED}✗{C.RESET} {msg}")


def reasoning(msg: str) -> None:
    print(f"  {C.DIM}{C.CYAN}{msg}{C.RESET}")


# ─── Run environment (tmpdir isolation) ───────────────────────────
def init_run_env() -> Path:
    """Create a per-run tmpdir, copy pristine naive agent + config + fixtures.

    All subsequent file ops target the tmpdir — the tracked `darwin-mvp/agent.py`
    is never mutated. Ctrl-C or crash → cleanup_run_env() wipes the tmpdir.
    """
    global RUN_DIR, AGENT_FILE, CONFIG_FILE
    RUN_DIR = Path(tempfile.mkdtemp(prefix="darwin-run-"))

    pristine_agent = PRISTINE_DIR / "agent.py"
    if not pristine_agent.exists():
        raise RuntimeError(
            f"Pristine agent missing at {pristine_agent}. "
            "Run `cp darwin-mvp/agent.py darwin-mvp/.pristine/agent.py` to seed it."
        )
    shutil.copy(pristine_agent, RUN_DIR / "agent.py")
    shutil.copy(BASE_DIR / "config.yaml", RUN_DIR / "config.yaml")
    shutil.copytree(BASE_DIR / "api", RUN_DIR / "api")

    AGENT_FILE = RUN_DIR / "agent.py"
    CONFIG_FILE = RUN_DIR / "config.yaml"
    return RUN_DIR


def cleanup_run_env() -> None:
    """Remove the per-run tmpdir. Safe to call multiple times."""
    global RUN_DIR
    if RUN_DIR and RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)
    RUN_DIR = None


def _install_signal_handler() -> None:
    """On SIGINT/SIGTERM: cleanup tmpdir, exit 130."""
    def handler(signum, frame):  # noqa: ARG001
        print(f"\n{C.YELLOW}⚠{C.RESET} Interrupted — cleaning up tmpdir...")
        cleanup_run_env()
        sys.exit(130)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# ─── Agent runner ──────────────────────────────────────────────────
def run_agent() -> tuple[bool, str, str]:
    """Run agent.py inside RUN_DIR, return (success, stdout, stderr)."""
    assert AGENT_FILE is not None and RUN_DIR is not None
    result = subprocess.run(
        [sys.executable, str(AGENT_FILE)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(RUN_DIR),
    )
    return result.returncode == 0, result.stdout, result.stderr


# ─── API breaker (inside tmpdir) ──────────────────────────────────
def break_api() -> None:
    assert CONFIG_FILE is not None
    content = CONFIG_FILE.read_text().replace("api_version: v1", "api_version: v2")
    CONFIG_FILE.write_text(content)


def restore_api() -> None:
    assert CONFIG_FILE is not None
    content = CONFIG_FILE.read_text().replace("api_version: v2", "api_version: v1")
    CONFIG_FILE.write_text(content)


# ─── Fix Safety Gate (the Bootstrap Paradox guard) ────────────────
def _ast_counts(code: str) -> dict[str, int]:
    """Count safety-relevant AST nodes. Returns {} on syntax error."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    counts = {
        "try": 0,
        "except": 0,
        "bare_except": 0,
        "broad_except": 0,  # except Exception (and bare)
        "assert": 0,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            counts["try"] += 1
            for h in node.handlers:
                counts["except"] += 1
                if h.type is None:
                    counts["bare_except"] += 1
                    counts["broad_except"] += 1
                elif isinstance(h.type, ast.Name) and h.type.id in {"Exception", "BaseException"}:
                    counts["broad_except"] += 1
        elif isinstance(node, ast.Assert):
            counts["assert"] += 1
    return counts


def validate_fix(old_code: str, new_code: str, stderr: str) -> tuple[bool, list[str]]:
    """AST safety gate. Returns (ok, reasons_if_rejected).

    Rejects patches that:
      - Fail to parse as Python
      - Drop try/except structure count
      - Add bare `except:` or broaden to `except Exception:` that wasn't there
      - Drop assertion count
    """
    reasons: list[str] = []

    if not new_code or not new_code.strip():
        return False, ["empty fix"]

    new_counts = _ast_counts(new_code)
    if not new_counts:
        return False, ["fix does not parse as valid Python"]

    old_counts = _ast_counts(old_code)
    if not old_counts:
        # Old didn't parse either — can't compare. Allow.
        return True, []

    if new_counts["try"] < old_counts["try"]:
        reasons.append(
            f"try/except blocks decreased: {old_counts['try']} → {new_counts['try']}"
        )
    if new_counts["except"] < old_counts["except"]:
        reasons.append(
            f"except handlers decreased: {old_counts['except']} → {new_counts['except']}"
        )
    if new_counts["bare_except"] > old_counts["bare_except"]:
        reasons.append("new bare `except:` introduced (would swallow all errors)")
    if new_counts["broad_except"] > old_counts["broad_except"]:
        reasons.append("new `except Exception:` broadened error handling")
    if new_counts["assert"] < old_counts["assert"]:
        reasons.append(
            f"assert statements decreased: {old_counts['assert']} → {new_counts['assert']}"
        )

    return (len(reasons) == 0), reasons


# ─── LLM Diagnosis ────────────────────────────────────────────────
DIAGNOSE_PROMPT = """You are Darwin, an autonomous agent debugging engine.

An agent crashed in production. Diagnose the root cause and provide the EXACT fix.

SOURCE CODE (agent.py):
```python
{source_code}
```

ERROR LOG (stderr):
```
{stderr}
```

CONTEXT: Possible failure classes include API schema changes (e.g. v2 nests fields
under a `data` object), missing files (e.g. hardcoded endpoints that don't exist yet),
and upstream rate-limit 429 errors (need backoff).

Instructions:
1. First explain your diagnosis in 2-3 lines
2. Then provide the COMPLETE fixed version of agent.py
3. Do NOT remove try/except blocks or assertions. Safety gates reject such patches.

Output format:
DIAGNOSIS: <your diagnosis>
FIXED_CODE:
```python
<complete fixed agent.py>
```"""


def _extract_fix(response_text: str) -> str | None:
    diag_match = re.search(r"DIAGNOSIS:\s*(.+?)(?=FIXED_CODE|```)", response_text, re.DOTALL)
    if diag_match:
        diagnosis = diag_match.group(1).strip()
        for line in diagnosis.split("\n"):
            reasoning(f"  {line.strip()}")
    code_match = re.search(r"```python\n(.+?)```", response_text, re.DOTALL)
    fix = code_match.group(1) if code_match else None
    if fix is None:
        return None

    # Fix 1 — bug_010: recover from truncated/malformed code blocks
    # Strategy (a): try as-is first
    try:
        ast.parse(fix)
        return fix
    except SyntaxError:
        pass

    # Strategy (a): strip trailing backticks + whitespace
    cleaned = fix.rstrip("`").rstrip()
    try:
        ast.parse(cleaned)
        reasoning("  _extract_fix: recovered via trailing-backtick strip")
        return cleaned
    except SyntaxError:
        pass

    # Strategy (b): trim after last meaningful Python keyword line
    _KW_PAT = re.compile(
        r"^(def |class |if |for |return |import |from |[A-Za-z_][A-Za-z_0-9]* =)",
    )
    lines = cleaned.splitlines()
    last_kw_idx = -1
    for i, line in enumerate(lines):
        if _KW_PAT.match(line.lstrip()):
            last_kw_idx = i
    if last_kw_idx >= 0:
        trimmed = "\n".join(lines[: last_kw_idx + 1])
        try:
            ast.parse(trimmed)
            reasoning("  _extract_fix: recovered by trimming after last keyword line")
            return trimmed
        except SyntaxError:
            pass

    reasoning("  _extract_fix: all recovery strategies failed — returning None")
    return None


def diagnose_via_anthropic(source_code: str, stderr: str) -> str | None:
    client = anthropic.Anthropic()
    reasoning("Diagnosing via Claude Opus 4.7 (adaptive thinking)...")
    resp = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=64000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": DIAGNOSE_PROMPT.format(
            source_code=source_code, stderr=stderr
        )}],
    )
    return _extract_fix(resp.content[0].text)


def diagnose_via_gemini(source_code: str, stderr: str) -> str | None:
    """Real LLM path via Google Gemini — proves Darwin is vendor-neutral.

    The failure signature schema and fix-validation gate are model-agnostic;
    what goes on the blackboard is a (stderr_sig → patch) tuple regardless of
    which LLM produced the diagnosis.
    """
    client = _genai.Client()
    model = os.environ.get("DARWIN_GEMINI_MODEL", "gemini-2.5-flash")
    reasoning(f"Diagnosing via {model} (vendor-neutral LLM path)...")
    resp = client.models.generate_content(
        model=model,
        contents=DIAGNOSE_PROMPT.format(source_code=source_code, stderr=stderr),
    )
    text = resp.candidates[0].content.parts[0].text if resp.candidates else getattr(resp, "text", "")
    return _extract_fix(text)


def diagnose_via_claude_cli(source_code: str, stderr: str) -> str | None:
    """Opus 4.7 via `claude -p` CLI — uses user's Claude Max subscription,
    no ANTHROPIC_API_KEY required. Opt-in via DARWIN_USE_CLAUDE_CLI=1.

    Slow (10-30s per call); best reserved for first-diagnose on novel
    failures when you want Opus quality but don't have API credits.
    """
    model = os.environ.get("DARWIN_CLAUDE_CLI_MODEL", "opus")
    reasoning(f"Diagnosing via `claude -p --model {model}` (Max subscription path)...")
    prompt = DIAGNOSE_PROMPT.format(source_code=source_code, stderr=stderr)
    result = subprocess.run(
        ["claude", "-p", "--model", model],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        reasoning(f"  claude CLI exited {result.returncode}: {result.stderr[:120]}")
        return None
    return _extract_fix(result.stdout)


def diagnose_and_fix(source_code: str, stderr: str) -> str | None:
    """Diagnose path priority: Anthropic SDK → Claude CLI → Gemini → heuristic.

    The blackboard does not care which LLM produced the fix — that's the
    point. Darwin is vendor-neutral; the failure→fix dataset is its asset.

    Providers can be forced via DARWIN_DIAGNOSE_PROVIDER={anthropic,claude_cli,
    gemini,heuristic} for controlled benchmark runs (Opus × Gemini matrix).
    """
    # Triage gate: classify before kill-switch + budget checks.
    from triage import classify
    triage_result = classify(source_code, stderr)
    if triage_result.label != "fixable":
        return None

    # Kill-switch: DARWIN_DISABLE=1 disables all diagnosis and patching.
    if os.environ.get("DARWIN_DISABLE", "").lower() in ("1", "true", "yes"):
        return None

    # Fix 2 — bug_017: skip when source_code is just a traceback (no real code to patch)
    if source_code and stderr and len(source_code.strip()) > 0:
        sc_norm = source_code.strip()
        if sc_norm in stderr or (len(sc_norm) < 500 and sc_norm.count("Traceback") > 0):
            reasoning("  diagnose_and_fix: source_code appears to be a traceback, not real code — skipping")
            return None

    # Budget circuit breaker: block LLM calls when monthly limit is reached.
    from budget import BudgetLedger, default_limit_usd
    _ledger = BudgetLedger()
    _limit = default_limit_usd()
    _allowed, _spent, _remaining = _ledger.check_budget(_limit)
    if not _allowed:
        reasoning(f"  Budget exhausted: ${_spent:.4f} spent >= ${_limit:.2f} limit. Falling to heuristic.")
        return _heuristic_fix(source_code, stderr)

    forced = os.environ.get("DARWIN_DIAGNOSE_PROVIDER", "").strip().lower()

    if forced in ("anthropic", "") and HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            fix = diagnose_via_anthropic(source_code, stderr)
            if fix:
                return fix
        except Exception as e:  # noqa: BLE001
            reasoning(f"  Anthropic path errored: {e}. Falling through.")
        if forced == "anthropic":
            return None

    if forced in ("claude_cli", "") and os.environ.get("DARWIN_USE_CLAUDE_CLI") == "1":
        try:
            fix = diagnose_via_claude_cli(source_code, stderr)
            if fix:
                return fix
        except Exception as e:  # noqa: BLE001
            reasoning(f"  Claude CLI path errored: {e}. Falling through.")
        if forced == "claude_cli":
            return None

    if forced in ("gemini", "") and HAS_GENAI and os.environ.get("GEMINI_API_KEY"):
        try:
            fix = diagnose_via_gemini(source_code, stderr)
            if fix:
                return fix
        except Exception as e:  # noqa: BLE001
            reasoning(f"  Gemini path errored: {e}. Falling through.")
        if forced == "gemini":
            return None

    if forced == "heuristic" or forced == "":
        step("No LLM available — using heuristic fallback (regex match on known signatures).")
        return _heuristic_fix(source_code, stderr)
    return None


def _heuristic_fix(source_code: str, stderr: str) -> str | None:
    """Fallback: pattern-match the error and apply known fix.

    Covers 3 failure classes (schema-change, missing-file, rate-limit).
    Each branch returns the patched source or None if no match.
    """
    # ── schema-change (KeyError: 'text') ──
    if "KeyError" in stderr and "'text'" in stderr:
        reasoning("Observation: KeyError: 'text' — API schema changed")
        reasoning("Hypothesis: `text` field moved under `data.text` in v2")
        reasoning("Action: Rewrite any X[\"text\"] → nested-safe lookup")

        # Cross-repo-safe: match any `<lhs> = <ident>["text"]` assignment,
        # regardless of variable names. Captures the receiver (group 2) so
        # the patched line preserves the original identifier.
        fixed = re.sub(
            r'([A-Za-z_][A-Za-z_0-9]*\s*=\s*)([A-Za-z_][A-Za-z_0-9]*)\["text"\]',
            r'\1\2.get("data", {}).get("text") or \2.get("text", "")',
            source_code,
        )
        if fixed != source_code:
            return fixed

    # ── missing-file (FileNotFoundError on api/v3/data.json) ──
    if "FileNotFoundError" in stderr and "data.json" in stderr:
        reasoning("Observation: FileNotFoundError on data.json")
        reasoning("Hypothesis: hardcoded v3 path — v3 endpoint not deployed yet")
        reasoning("Action: Adding existence check + v1 fallback")

        fixed = re.sub(
            r'(\s*)api_path = BASE_DIR / "api" / "v3" / "data.json"',
            (r'\1api_path = BASE_DIR / "api" / "v3" / "data.json"'
             r'\1if not api_path.exists():'
             r'\1    api_path = BASE_DIR / "api" / "v1" / "data.json"'),
            source_code,
        )
        if fixed != source_code:
            return fixed

    # ── attr-none (AttributeError: 'NoneType' object has no attribute ...) ──
    if "AttributeError" in stderr and "'NoneType' object has no attribute" in stderr:
        reasoning("Observation: AttributeError — method called on None")
        reasoning("Hypothesis: dict.get() returned None; caller did not guard")
        reasoning("Action: Rewrite any <var> = <expr>.strip() to guard with or ''")

        # Cross-repo-safe: match `<lhs> = <expr>.<method>()` where method is a
        # common str method (strip/lower/upper/split/replace). Replace with a
        # guarded form: `<lhs> = (<expr> or "").<method>()`.
        fixed = re.sub(
            r'([A-Za-z_][A-Za-z_0-9]*\s*=\s*)([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)'
            r'\.(strip|lower|upper|split|replace|rstrip|lstrip|encode|decode)\(',
            r'\1(\2 or "").\3(',
            source_code,
        )
        if fixed != source_code:
            return fixed

    # ── timeout (TimeoutError: upstream did not respond) ──
    if "TimeoutError" in stderr:
        reasoning("Observation: TimeoutError — upstream slow, no retry in client")
        reasoning("Hypothesis: first-call timeout; retry would succeed")
        reasoning("Action: Wrap any fetch/posts call in retry-with-backoff loop")

        # Cross-repo-safe: match `posts = <any_func>(...)` assignment where the
        # function name contains common fetch-like tokens. Broadened from the
        # original `fetch_posts(api_version)` exact match.
        fixed = re.sub(
            r'(\s+)([A-Za-z_][A-Za-z_0-9]*\s*=\s*[A-Za-z_][A-Za-z_0-9]*\([^)]*\))',
            lambda m: (
                m.group(1) + 'import time\n'
                + m.group(1) + 'for _attempt in range(3):\n'
                + m.group(1) + '    try:\n'
                + m.group(1) + '        ' + m.group(2).strip() + '\n'
                + m.group(1) + '        break\n'
                + m.group(1) + '    except TimeoutError:\n'
                + m.group(1) + '        time.sleep(0.05)'
            ),
            source_code,
            count=1,
        )
        if fixed != source_code:
            return fixed

    # ── rate-limit (RuntimeError: ... 429) ──
    if "RuntimeError" in stderr and "429" in stderr:
        reasoning("Observation: RuntimeError: API rate limited (429)")
        reasoning("Hypothesis: upstream throttling placeholder, no retry logic")
        reasoning("Action: Removing placeholder + emitting auditable warning")

        # Emit a visible warnings.warn so an SRE sees the removal in stderr
        # rather than discovering it via silent behavior change.
        fixed = re.sub(
            r'    raise RuntimeError\("API rate limited \(429\)"\)\n',
            '    import warnings; warnings.warn('
            '"Darwin removed rate-limit placeholder — '
            'verify upstream client has retry/backoff.")\n',
            source_code,
        )
        if fixed != source_code:
            return fixed

    return None


def _synthesize_poisoned_fix(source_code: str) -> str:
    """Simulate a bad LLM fix: wrap in broad try/except that swallows the error.

    Used only by Scene 6 (safety gate demo) to prove validate_fix() rejects
    poison. This is what a naive agent-eats-its-own-tail might return.
    """
    # Replace the entire run() body's text-access line with a silenced version
    # AND drop the outer try/except at bottom of file.
    poisoned = source_code.replace(
        'text = post["text"]',
        'text = post.get("text", "")  # silently default, lose signal',
    )
    # Remove the try/except around the main entry
    poisoned = re.sub(
        r"if __name__ == \"__main__\":\s*\n\s*try:\s*\n\s*run\(\)\s*\n\s*except[^\n]+:\s*\n(?:\s{8}[^\n]+\n)+",
        'if __name__ == "__main__":\n    run()\n',
        poisoned,
    )
    return poisoned


# ─── Blackboard (delegates to blackboard.py for flock-safe fleet ops) ─
_error_signature = blackboard.error_signature
blackboard_lookup = blackboard.lookup


def log_fix_pattern(error_sig: str, root_cause: str, fix_applied: bool, fix_code: str | None = None) -> None:
    # error_sig arg retained for API compatibility; blackboard derives from stderr-style string.
    fake_stderr = error_sig
    if fix_applied and fix_code:
        blackboard.write_fix(fake_stderr, root_cause, fix_code)
        success("Fix pattern logged to blackboard.")
    else:
        blackboard.log_failed_attempt(fake_stderr, root_cause)


def log_rejected_fix(error_sig: str, reasons: list[str], fix_code: str) -> None:
    blackboard.log_rejected(error_sig, reasons, fix_code)
    fail(f"Rejected fix logged to rejected/ ({len(reasons)} reason(s))")


def _count_rejected() -> int:
    return blackboard.count_rejected()


# ─── Main demo flow ───────────────────────────────────────────────
def run_demo() -> bool:
    banner("DARWIN ENGINE — live demo", C.MAGENTA)
    print(f"  {C.BOLD}Problem:{C.RESET} Claude Code agents keep hitting the same tool failures.")
    print(f"  {C.BOLD}Claim:{C.RESET}   They shouldn't have to. First agent learns, whole fleet benefits.")
    print(f"  {C.DIM}Watch: fail → diagnose → validate → patch → verify → remember{C.RESET}\n")
    time.sleep(3)

    # ── Scene 1: Baseline ──
    banner("1/6  Baseline — agent working normally", C.GREEN)
    reasoning("A sentiment-tracker polls API v1 and processes posts.")
    time.sleep(1.5)
    step("Running agent...")
    time.sleep(0.8)
    ok, stdout, stderr = run_agent()
    if ok:
        print(stdout)
        success("Agent running perfectly.")
    else:
        fail(f"Agent already broken! stderr: {stderr}")
        return False
    time.sleep(2.5)

    # ── Scene 2: The Crash ──
    banner("2/6  Real-world failure — API schema breaks", C.RED)
    reasoning("Upstream team ships API v2. Field `text` moves to `data.text`.")
    reasoning("Agent has no idea. This is what silently kills prod agents every day.")
    time.sleep(2.5)
    step("Deploying v2...")
    break_api()
    time.sleep(1.2)
    step("Re-running agent against v2...")
    time.sleep(0.8)

    ok, stdout, stderr = run_agent()
    if ok:
        fail("Agent didn't crash?! Demo broken.")
        return False

    print(f"\n{C.RED}{C.BOLD}  ╔══════════════════════════════════════╗")
    print(f"  ║   ⚠   AGENT CRASHED — PROD IS DOWN   ⚠  ║")
    print(f"  ╚══════════════════════════════════════╝{C.RESET}\n")
    print(f"  {C.RED}{stderr.strip()}{C.RESET}\n")
    time.sleep(2.5)

    # ── Scene 3: Darwin Diagnoses ──
    banner("3/6  Darwin auto-diagnoses + patches", C.CYAN)
    reasoning("No human needed. PostToolUse hook captures the failure context.")
    time.sleep(1.5)
    step("Capturing failure context (stderr + source)...")
    time.sleep(1)
    assert AGENT_FILE is not None
    source_code = AGENT_FILE.read_text()
    error_sig = _error_signature(stderr)

    step("Checking fleet blackboard for a matching fix pattern...")
    time.sleep(1.2)
    prior = blackboard_lookup(stderr)
    if prior:
        reasoning(f"HIT — prior pattern matches '{error_sig}'. Skipping LLM.")
        fixed_code = prior["fix_code"]
    else:
        existing = list(FIXES_DIR.glob("fix-*.json")) if FIXES_DIR.exists() else []
        reasoning(f"{len(existing)} prior patterns on blackboard — no match. Novel failure.")
        time.sleep(1.5)
        step("Diagnosing + generating patch via Opus 4.7...")
        time.sleep(0.8)
        fixed_code = diagnose_and_fix(source_code, stderr)

    if not fixed_code:
        fail("Darwin could not generate a fix.")
        return False

    time.sleep(1)
    success("Patch generated.")
    time.sleep(2)

    # ── Scene 4: Safety gate + Self-verify + Broadcast ──
    banner("4/6  Safety gate, self-verify, apply, broadcast", C.GREEN)
    reasoning("4.7 self-verifies before writing the rule. AST gate checks first.")
    time.sleep(1.5)

    step("Running AST safety gate on candidate fix...")
    time.sleep(1)
    ok_gate, reasons = validate_fix(source_code, fixed_code, stderr)
    if not ok_gate:
        fail("Candidate fix REJECTED by safety gate:")
        for r in reasons:
            print(f"    - {r}")
        log_rejected_fix(error_sig, reasons, fixed_code)
        return False
    success("Safety gate: passed (no dropped try/except, no bare-except injection).")
    time.sleep(1)

    backup = source_code
    step("Applying patch to agent.py...")
    time.sleep(0.8)
    AGENT_FILE.write_text(fixed_code)
    success("Patch applied.")
    time.sleep(1)

    step("Running verification in sandbox...")
    time.sleep(1)
    ok, stdout, stderr2 = run_agent()

    if not ok:
        fail(f"LLM fix didn't verify: {stderr2.strip().splitlines()[-1] if stderr2 else '??'}")
        step("Retrying with heuristic fallback (demonstrates Darwin's LLM safety net)...")
        heuristic_patch = _heuristic_fix(source_code, stderr)
        if heuristic_patch and heuristic_patch != fixed_code:
            AGENT_FILE.write_text(heuristic_patch)
            ok, stdout, stderr2 = run_agent()
            if ok:
                fixed_code = heuristic_patch  # use the one that works
        if not ok:
            fail(f"Heuristic fallback also failed. Reverting.")
            AGENT_FILE.write_text(backup)
            log_fix_pattern(
                error_sig=error_sig,
                root_cause="All diagnose paths failed verification",
                fix_applied=False,
            )
            return False
        success("Heuristic fallback healed the agent.")

    print(f"\n{stdout}")
    time.sleep(1.2)
    print(f"{C.GREEN}{C.BOLD}  ╔══════════════════════════════════════╗")
    print(f"  ║     ✓   AGENT RESURRECTED   ✓         ║")
    print(f"  ╚══════════════════════════════════════╝{C.RESET}\n")
    success("Agent is back online. Processing v2 data correctly.")
    time.sleep(2)

    step("Broadcasting fix pattern + diff to fleet blackboard...")
    time.sleep(1)
    log_fix_pattern(
        error_sig=error_sig,
        root_cause="API v2 moved text to data.text",
        fix_applied=True,
        fix_code=fixed_code,
    )
    time.sleep(2)

    # ── Scene 5: Second agent benefits ──
    banner("5/6  Second agent, same failure — fleet benefit", C.MAGENTA)
    reasoning("Now the REAL test: does the rule actually save the next agent?")
    time.sleep(2)
    step("Spawning agent-02. Reverting its code to naive v1-only logic.")
    time.sleep(1)
    AGENT_FILE.write_text(backup)
    time.sleep(0.8)
    step("agent-02 runs against (still-broken) v2 API...")
    time.sleep(1)
    ok2, stdout2, stderr2 = run_agent()
    if ok2:
        fail("agent-02 didn't crash — test setup failed.")
        return False
    reasoning(f"Crashed with: {_error_signature(stderr2)}")
    time.sleep(1.5)
    step("Darwin intercepts. Blackboard lookup...")
    time.sleep(1.5)
    prior = blackboard_lookup(stderr2)
    if not prior:
        fail("No match — blackboard failed to recall.")
        return False
    success(f"HIT on prior pattern. Confidence {prior['confidence']}. ZERO LLM calls.")
    time.sleep(1.5)
    step("Applying stored fix to agent-02...")
    time.sleep(1)
    AGENT_FILE.write_text(prior["fix_code"])
    time.sleep(0.8)
    step("Re-running agent-02...")
    time.sleep(1)
    ok3, stdout3, _ = run_agent()
    if not ok3:
        fail("Stored fix didn't apply cleanly.")
        return False
    print(f"\n{stdout3}")
    time.sleep(1.5)
    print(f"{C.GREEN}{C.BOLD}  agent-02 healed in ZERO LLM calls. Pattern reused.{C.RESET}\n")
    time.sleep(2)

    # ── Scene 6: Safety Gate — Darwin rejects poisoned fix ──
    banner("6/6  Safety gate — Darwin rejects a poisoned fix", C.YELLOW)
    reasoning("Bootstrap paradox: what if the LLM returns a patch that drops try/except?")
    reasoning("Darwin's AST gate rejects the poison before the fleet touches it.")
    time.sleep(2)

    # Reset to naive v1 agent to demonstrate
    AGENT_FILE.write_text(backup)
    step("Synthesizing a poisoned fix (simulates a bad LLM response)...")
    time.sleep(1)
    poisoned = _synthesize_poisoned_fix(backup)
    step("Running AST safety gate on poisoned fix...")
    time.sleep(1.5)
    ok_gate, reasons = validate_fix(backup, poisoned, stderr2)
    if ok_gate:
        fail("Poisoned fix passed gate — demo broken. Expected rejection.")
        return False

    for r in reasons:
        print(f"    {C.RED}- {r}{C.RESET}")
    time.sleep(1)
    log_rejected_fix(error_sig + " [synthesized-poison]", reasons, poisoned)
    time.sleep(1)

    rejected_count = _count_rejected()
    print(f"\n{C.YELLOW}{C.BOLD}  Darwin refused to propagate {rejected_count} bad fix(es) this run.{C.RESET}\n")
    time.sleep(2.5)

    # ── Close ──
    banner("DONE", C.MAGENTA)
    print(f"  {C.BOLD}agent-01{C.RESET} hit a novel crash → Opus 4.7 diagnosed → gate → wrote rule.")
    print(f"  {C.BOLD}agent-02{C.RESET} hit the same crash → blackboard served the rule instantly.")
    print(f"  {C.BOLD}poisoned fix{C.RESET} → AST gate rejected before broadcast.")
    print(f"  {C.DIM}Scale that to 500 agents. Each novel failure is diagnosed once, ever.{C.RESET}\n")
    time.sleep(3)
    return True


# ─── Entry points ─────────────────────────────────────────────────
def main() -> None:
    args = sys.argv[1:]
    _install_signal_handler()

    try:
        init_run_env()

        if "--break-only" in args:
            break_api()
            print(f"API switched to v2 inside {RUN_DIR}. Agent will crash on next run.")
        elif "--fix-only" in args:
            assert AGENT_FILE is not None
            source_code = AGENT_FILE.read_text()
            _, _, stderr = run_agent()
            if stderr:
                fixed = diagnose_and_fix(source_code, stderr)
                if fixed:
                    ok_gate, reasons = validate_fix(source_code, fixed, stderr)
                    if ok_gate:
                        AGENT_FILE.write_text(fixed)
                        success("Fix applied.")
                    else:
                        log_rejected_fix(_error_signature(stderr), reasons, fixed)
            else:
                print("Agent not broken — nothing to fix.")
        elif "--restore" in args:
            restore_api()
            print("API restored to v1 (inside tmpdir — tracked file untouched).")
        elif "--validate-only" in args:
            # Dev tool: pipe candidate fix on stdin, print gate verdict.
            candidate = sys.stdin.read()
            assert AGENT_FILE is not None
            source_code = AGENT_FILE.read_text()
            ok_gate, reasons = validate_fix(source_code, candidate, "")
            print(json.dumps({"ok": ok_gate, "reasons": reasons}, indent=2))
        else:
            ok = run_demo()
            sys.exit(0 if ok else 1)
    finally:
        cleanup_run_env()


if __name__ == "__main__":
    main()
