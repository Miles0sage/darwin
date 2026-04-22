"""
Darwin Triage Classifier — labels every incoming failure before the patch loop runs.

Labels:
  FIXABLE      — Darwin should attempt heal
  FLAKY        — transient; do NOT patch, suggest retry
  HUMAN_NEEDED — needs human judgement; do NOT patch

Order of precedence: FLAKY > HUMAN_NEEDED > FIXABLE
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone


class TriageLabel:
    FIXABLE = "fixable"
    FLAKY = "flaky"
    HUMAN_NEEDED = "human_needed"


@dataclass
class TriageResult:
    label: str
    confidence: float
    reason: str
    features: dict = field(default_factory=dict)


# ─── FLAKY patterns ───────────────────────────────────────────────────────────
_FLAKY_EXCEPTION_TYPES = (
    "TimeoutError",
    "asyncio.TimeoutError",
    "ConnectionError",
    "socket.timeout",
    "ConnectionResetError",
    "ReadTimeout",
    "ssl.SSLWantReadError",
)

_FLAKY_STRINGS = (
    "rate limit",
    "503 UNAVAILABLE",
    "429 Too Many Requests",
    "connection reset by peer",
    "EPIPE",
)

# ─── HUMAN_NEEDED patterns ────────────────────────────────────────────────────
_SYSTEM_PATHS = ("/etc/", "/root/", "/var/log/", "/sys/", "/dev/")

_PLATFORM_STRINGS = (
    "architecture mismatch",
    "arm64",
    "x86_64",
    "rosetta",
)

_HUMAN_STRINGS = (
    "license",
    "authentication required",
    "403 Forbidden",
)


def classify(source_code: str, stderr: str) -> TriageResult:
    """Heuristic triage — no LLM call, no external deps.

    Checks FLAKY first (most reversible), then HUMAN_NEEDED, then defaults to FIXABLE.
    """
    combined = stderr  # source_code reserved for future structural analysis
    features: dict = {}

    # ── FLAKY: transient network / rate-limit errors ──────────────────────────
    for exc in _FLAKY_EXCEPTION_TYPES:
        if exc in combined:
            features["matched_exception"] = exc
            return TriageResult(
                label=TriageLabel.FLAKY,
                confidence=0.85,
                reason=f"Transient error detected: {exc}",
                features=features,
            )

    for pattern in _FLAKY_STRINGS:
        if pattern.lower() in combined.lower():
            features["matched_pattern"] = pattern
            return TriageResult(
                label=TriageLabel.FLAKY,
                confidence=0.85,
                reason=f"Transient signal detected: '{pattern}'",
                features=features,
            )

    # ── HUMAN_NEEDED: system-level / platform / auth issues ──────────────────
    if "PermissionError" in combined:
        for sys_path in _SYSTEM_PATHS:
            if sys_path in combined:
                features["system_path"] = sys_path
                return TriageResult(
                    label=TriageLabel.HUMAN_NEEDED,
                    confidence=0.75,
                    reason=f"PermissionError on system path '{sys_path}' — requires elevated access",
                    features=features,
                )

    for platform_str in _PLATFORM_STRINGS:
        if platform_str.lower() in combined.lower():
            features["platform_signal"] = platform_str
            return TriageResult(
                label=TriageLabel.HUMAN_NEEDED,
                confidence=0.75,
                reason=f"Platform/architecture issue detected: '{platform_str}'",
                features=features,
            )

    for human_str in _HUMAN_STRINGS:
        if human_str.lower() in combined.lower():
            features["human_signal"] = human_str
            return TriageResult(
                label=TriageLabel.HUMAN_NEEDED,
                confidence=0.75,
                reason=f"Human-gated condition detected: '{human_str}'",
                features=features,
            )

    if "MemoryError" in combined:
        features["error_type"] = "MemoryError"
        return TriageResult(
            label=TriageLabel.HUMAN_NEEDED,
            confidence=0.75,
            reason="MemoryError on heap-critical path — requires capacity decision",
            features=features,
        )

    if "IntegrityError" in combined and "foreign key" in combined.lower():
        features["error_type"] = "IntegrityError+FK"
        return TriageResult(
            label=TriageLabel.HUMAN_NEEDED,
            confidence=0.75,
            reason="IntegrityError with FK reference — schema decision required",
            features=features,
        )

    # ── FIXABLE: default — let Darwin try ────────────────────────────────────
    return TriageResult(
        label=TriageLabel.FIXABLE,
        confidence=0.6,
        reason="No flaky or human-gated signal detected — Darwin will attempt heal",
        features=features,
    )


_TRIAGE_LOG = "/tmp/darwin-triage.jsonl"


def triage_and_log(
    source_code: str,
    stderr: str,
    receipt_path: str | None = None,
) -> TriageResult:
    """Classify and optionally append audit record to JSONL log."""
    result = classify(source_code, stderr)
    log_path = receipt_path or _TRIAGE_LOG
    try:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "label": result.label,
            "confidence": result.confidence,
            "reason": result.reason,
        }
        with open(log_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # audit log failure must not block the heal loop
    return result


__all__ = [
    "TriageLabel",
    "TriageResult",
    "classify",
    "triage_and_log",
]
