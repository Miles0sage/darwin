#!/usr/bin/env python3
"""
Darwin Blackboard — shared failure→fix memory across a fleet of agent processes.

Design:
  - One directory per blackboard (DARWIN_FIXES_DIR env var or default).
  - Reads are lock-free (sorted glob — concurrent reads are fine).
  - Writes go through fcntl.flock on <FIXES_DIR>/.write-lock.
  - `compute_and_write_fix()` is the fleet-race primitive:
        * exclusive lock → re-check lookup → if still missing, call supplied
          `compute()` and write. First miss wins; N-1 workers read the result.

This is what turns the "agent fleet" pitch from a for-loop into a real
multi-process coordination primitive.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from signature import fingerprint as _fingerprint, error_class as _error_class

_DEFAULT_FIXES = Path(__file__).parent / "fixes"
FIXES_DIR: Path = Path(os.environ.get("DARWIN_FIXES_DIR", _DEFAULT_FIXES))


def set_fixes_dir(path: str | Path) -> None:
    """Override at runtime (mainly for tests)."""
    global FIXES_DIR
    FIXES_DIR = Path(path)


def _rejected_dir() -> Path:
    return FIXES_DIR / "rejected"


def _lock_path() -> Path:
    return FIXES_DIR / ".write-lock"


def _ensure_dirs() -> None:
    FIXES_DIR.mkdir(parents=True, exist_ok=True)
    _rejected_dir().mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def exclusive_lock():
    """Hold an exclusive flock on FIXES_DIR/.write-lock for the block."""
    _ensure_dirs()
    lock_file = _lock_path()
    lock_file.touch(exist_ok=True)
    with open(lock_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


_TMP_PREFIX = re.compile(r"/tmp/darwin-[^/]+/")


def error_signature(stderr: str) -> str:
    """Stable signature for blackboard matching.

    Normalizes per-worker tmpdir paths so `FileNotFoundError` and friends
    match across the fleet. Without this, each worker's unique /tmp/darwin-
    worker-XXX/ prefix would make every error signature distinct → zero
    cache hits on any path-including error.
    """
    match = re.search(r"([A-Za-z_]+Error: [^\n]+)", stderr)
    if match:
        sig = match.group(1).strip()
        sig = _TMP_PREFIX.sub("", sig)
        return sig
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else stderr.strip()


def lookup(stderr: str) -> dict | None:
    """Read-only lookup. Primary key = cross-codebase fingerprint; falls back
    to the legacy string-signature match for entries written before the
    fingerprint upgrade.

    Cross-repo transfer: a fix captured in repo A (with its paths/filenames)
    will match a new failure in repo B with a different filename but the same
    error class + terminal code line.
    """
    if not FIXES_DIR.exists():
        return None
    fp, _ = _fingerprint(stderr)
    sig = error_signature(stderr)
    for path in sorted(FIXES_DIR.glob("fix-*.json")):
        try:
            entry = json.loads(path.read_text())
        except Exception:
            continue
        if not (entry.get("fix_applied") and entry.get("fix_code")):
            continue
        if entry.get("fingerprint") == fp:
            return entry
        # Legacy fallback for entries written without fingerprint
        if "fingerprint" not in entry and entry.get("error_signature") == sig:
            return entry
    return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def write_fix(
    stderr: str,
    root_cause: str,
    fix_code: str,
    originating_agent: str = "sentiment-tracker",
    llm_provider: str | None = None,
) -> dict:
    """Unconditional write (caller holds the lock or races are acceptable)."""
    _ensure_dirs()
    sig = error_signature(stderr)
    fp, _ = _fingerprint(stderr)
    entry = {
        "fingerprint": fp,
        "error_class": _error_class(stderr),
        "error_signature": sig,
        "root_cause": root_cause,
        "fix_applied": True,
        "fix_code": fix_code,
        "timestamp": _timestamp(),
        "originating_agent": originating_agent,
        "llm_provider": llm_provider or os.environ.get("DARWIN_LLM_PROVIDER", "unknown"),
        "confidence": 0.95,
    }
    path = FIXES_DIR / f"fix-{entry['timestamp']}.json"
    path.write_text(json.dumps(entry, indent=2))
    return entry


def log_failed_attempt(stderr: str, root_cause: str) -> None:
    """Record an attempted-but-failed fix (verification didn't pass)."""
    _ensure_dirs()
    fp, _ = _fingerprint(stderr)
    entry = {
        "fingerprint": fp,
        "error_class": _error_class(stderr),
        "error_signature": error_signature(stderr),
        "root_cause": root_cause,
        "fix_applied": False,
        "timestamp": _timestamp(),
        "confidence": 0.0,
    }
    path = FIXES_DIR / f"fix-{entry['timestamp']}.json"
    path.write_text(json.dumps(entry, indent=2))


def log_rejected(stderr: str, reasons: list[str], fix_code: str) -> None:
    """Rejected-fix ledger (validate_fix gate said no)."""
    _ensure_dirs()
    fp, _ = _fingerprint(stderr)
    entry = {
        "fingerprint": fp,
        "error_class": _error_class(stderr),
        "error_signature": error_signature(stderr),
        "rejection_reasons": reasons,
        "fix_code": fix_code,
        "timestamp": _timestamp(),
        "gate": "validate_fix",
    }
    path = _rejected_dir() / f"rejected-{entry['timestamp']}.json"
    path.write_text(json.dumps(entry, indent=2))


def compute_and_write_fix(
    stderr: str,
    compute,
    root_cause: str,
    originating_agent: str = "sentiment-tracker",
) -> tuple[bool, dict | None, list[str] | None]:
    """Fleet-race primitive.

    Under exclusive flock:
      1. re-check lookup() — if a prior worker already wrote, return their entry
      2. else call compute() → (fix_code|None, validate_ok, reasons)
      3. if validate_ok → write and return (was_first=True, entry, None)
      4. else → log_rejected, return (was_first=False, None, reasons)

    `compute` is `() -> (fix_code: str|None, ok_gate: bool, reasons: list[str])`.

    Returns (was_first, entry_or_None, reject_reasons_or_None).
    """
    with exclusive_lock():
        prior = lookup(stderr)
        if prior is not None:
            return False, prior, None

        fix_code, ok_gate, reasons = compute()
        if fix_code is None:
            return False, None, ["compute returned None"]
        if not ok_gate:
            log_rejected(stderr, reasons, fix_code)
            return False, None, reasons
        entry = write_fix(stderr, root_cause, fix_code, originating_agent)
        return True, entry, None


def count_fixes() -> int:
    if not FIXES_DIR.exists():
        return 0
    return len(list(FIXES_DIR.glob("fix-*.json")))


def count_rejected() -> int:
    if not _rejected_dir().exists():
        return 0
    return len(list(_rejected_dir().glob("rejected-*.json")))
