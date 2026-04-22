"""Tests for Darwin triage classifier."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from triage import TriageLabel, TriageResult, classify, triage_and_log


def test_classify_timeout_is_flaky() -> None:
    stderr = "TimeoutError: upstream did not respond within 30s"
    result = classify("x = 1", stderr)
    assert result.label == TriageLabel.FLAKY
    assert result.confidence >= 0.8
    assert "TimeoutError" in result.reason


def test_classify_permission_root_is_human_needed() -> None:
    stderr = "PermissionError: [Errno 13] Permission denied: '/root/.ssh/config'"
    result = classify("x = 1", stderr)
    assert result.label == TriageLabel.HUMAN_NEEDED
    assert result.confidence >= 0.7
    assert "/root/" in result.reason


def test_classify_nameerror_is_fixable() -> None:
    stderr = "NameError: name 'fetch_data' is not defined"
    result = classify("x = fetch_data()", stderr)
    assert result.label == TriageLabel.FIXABLE
    assert 0.0 < result.confidence <= 1.0


def test_triage_and_log_writes_jsonl() -> None:
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        result = triage_and_log("x = 1", "KeyError: 'text'", receipt_path=path)
        assert result.label == TriageLabel.FIXABLE
        with open(path) as fh:
            lines = [l.strip() for l in fh if l.strip()]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["label"] == TriageLabel.FIXABLE
        assert "timestamp" in record
        assert "reason" in record
    finally:
        os.remove(path)


def test_classify_confidence_is_in_range() -> None:
    cases = [
        ("x = 1", "ConnectionError: connection refused"),
        ("x = 1", "PermissionError: /etc/hosts"),
        ("x = 1", "NameError: foo not defined"),
        ("x = 1", "429 Too Many Requests"),
        ("x = 1", "MemoryError"),
    ]
    for source, stderr in cases:
        result = classify(source, stderr)
        assert 0.0 <= result.confidence <= 1.0, (
            f"confidence {result.confidence} out of range for: {stderr!r}"
        )
        assert result.label in (
            TriageLabel.FIXABLE,
            TriageLabel.FLAKY,
            TriageLabel.HUMAN_NEEDED,
        )
