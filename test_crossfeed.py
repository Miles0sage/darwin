"""Tests for Darwin Crossfeed — federated Q-delta protocol."""

import hashlib
import json
import os
import math
import pytest
import sys

sys.path.insert(0, os.path.dirname(__file__))

from crossfeed import (
    CrossfeedMessage,
    CrossfeedClient,
    sample_laplace,
    compute_q_delta,
    apply_q_delta,
    sign_message,
    verify_message,
    make_message,
    _payload_dict,
)
from patch import (
    PatchRecipe,
    try_apply,
    apply_recipe_from_crossfeed,
    export_recipe,
)
from signature import fingerprint as compute_fingerprint


def test_protocol_roundtrip() -> None:
    SECRET = b"test-secret-key"
    msg = CrossfeedMessage(fingerprint="abc123", patch_recipe="class Patch: pass", q_delta=0.5)
    payload = _payload_dict(msg)
    sig = sign_message(payload, SECRET)
    assert verify_message(payload, sig, SECRET) is True
    data = json.dumps({**payload, "hmac": sig})
    parsed = json.loads(data)
    assert parsed["fingerprint"] == "abc123"
    assert parsed["q_delta"] == 0.5


def test_laplace_bounds() -> None:
    N = 1000
    samples = [sample_laplace(epsilon=1.0) for _ in range(N)]
    mean = sum(samples) / N
    assert abs(mean) < 0.2, f"Laplace mean too far from 0: {mean}"
    within_8 = sum(1 for s in samples if abs(s) <= 8)
    assert within_8 >= 990, f"Only {within_8}/1000 within [-8, 8]"


def test_signature_mismatch() -> None:
    SECRET = b"test-secret-key"
    msg = CrossfeedMessage(fingerprint="xyz", success_count=1)
    payload = _payload_dict(msg)
    sig = sign_message(payload, SECRET)
    payload["success_count"] = 999
    assert verify_message(payload, sig, SECRET) is False


def test_recipe_apply_success() -> None:
    TRANSFORMER_SRC = '''
import libcst as cst

class Patch(cst.CSTTransformer):
    def leave_SimpleString(self, original_node, updated_node):
        if updated_node.value == '"hello"':
            return updated_node.with_changes(value='"world"')
        return updated_node
'''
    source = 'x = "hello"'
    crossfeed_msg = {"patch_recipe": TRANSFORMER_SRC}
    success, result, err = apply_recipe_from_crossfeed(source, crossfeed_msg)
    assert success is True, f"Expected success, got error: {err}"
    assert '"world"' in result
    assert err is None


def test_recipe_apply_missing_recipe() -> None:
    crossfeed_msg: dict = {}
    success, result, err = apply_recipe_from_crossfeed("x = 1", crossfeed_msg)
    assert success is False
    assert err is not None


def test_kill_switch_disables_heal() -> None:
    """DARWIN_DISABLE=1 must make diagnose_and_fix() return None immediately."""
    import darwin_harness
    old = os.environ.get("DARWIN_DISABLE")
    try:
        os.environ["DARWIN_DISABLE"] = "1"
        result = darwin_harness.diagnose_and_fix("x = 1", "KeyError: 'text'")
        assert result is None, "Kill-switch should return None, not a fix"
    finally:
        if old is None:
            os.environ.pop("DARWIN_DISABLE", None)
        else:
            os.environ["DARWIN_DISABLE"] = old


def test_whitelist_rejects_unknown_recipe() -> None:
    """With DARWIN_WHITELIST_ENFORCE=1 and an empty whitelist, apply_recipe_from_crossfeed must reject."""
    import hashlib
    from patch import apply_recipe_from_crossfeed
    old_enforce = os.environ.get("DARWIN_WHITELIST_ENFORCE")
    old_path = os.environ.get("DARWIN_WHITELIST_PATH")
    try:
        # Use a temp path that doesn't exist → empty whitelist
        os.environ["DARWIN_WHITELIST_ENFORCE"] = "1"
        os.environ["DARWIN_WHITELIST_PATH"] = "/tmp/darwin-whitelist-test-empty.json"
        # Remove test file if it exists
        try:
            os.remove("/tmp/darwin-whitelist-test-empty.json")
        except FileNotFoundError:
            pass
        transformer_src = 'class Patch(cst.CSTTransformer): pass'
        ast_sig = hashlib.sha256(transformer_src.encode()).hexdigest()
        msg = {
            "patch_recipe": transformer_src,
            "fingerprint": "test-fp-unknown",
            "ast_signature_hash": ast_sig,
        }
        success, _src, err = apply_recipe_from_crossfeed("x = 1", msg)
        assert success is False
        assert err is not None and "whitelist" in err.lower()
    finally:
        if old_enforce is None:
            os.environ.pop("DARWIN_WHITELIST_ENFORCE", None)
        else:
            os.environ["DARWIN_WHITELIST_ENFORCE"] = old_enforce
        if old_path is None:
            os.environ.pop("DARWIN_WHITELIST_PATH", None)
        else:
            os.environ["DARWIN_WHITELIST_PATH"] = old_path


def test_budget_circuit_breaker_blocks_overspend() -> None:
    """BudgetLedger.check_budget() must return allowed=False once spend exceeds limit."""
    import tempfile
    from budget import BudgetLedger
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        ledger = BudgetLedger(path=path)
        # Record a call that costs more than the $0.01 limit we'll use
        # opus input: $15/MTok — 1M tokens = $15 > $0.01
        ledger.record_call("claude-opus-4-7", tokens_in=1_000_000, tokens_out=0)
        allowed, spent, remaining = ledger.check_budget(limit_usd=0.01)
        assert allowed is False, f"Should be blocked; spent={spent:.4f}"
        assert spent > 0.01
        assert remaining == 0.0
    finally:
        os.remove(path)


def test_fingerprint_collision_same_bug() -> None:
    stderr_a = (
        "AttributeError: 'NoneType' object has no attribute 'text'\n"
        "  File 'repo_a/views.py', line 42, in get_user\n"
        "    return response.text.strip()"
    )
    stderr_b = (
        "AttributeError: 'NoneType' object has no attribute 'text'\n"
        "  File 'repo_b/models.py', line 17, in fetch_content\n"
        "    return obj.text.strip()"
    )
    fp_a, _ = compute_fingerprint(stderr_a)
    fp_b, _ = compute_fingerprint(stderr_b)
    assert fp_a == fp_b, f"Same bug should collide: {fp_a} != {fp_b}"


def test_harness_respects_triage() -> None:
    """diagnose_and_fix() must return None for FLAKY failures without calling any LLM."""
    import unittest.mock as mock
    import darwin_harness
    from triage import TriageResult, TriageLabel

    flaky_result = TriageResult(
        label=TriageLabel.FLAKY,
        confidence=0.85,
        reason="Transient error detected: TimeoutError",
        features={"matched_exception": "TimeoutError"},
    )

    with mock.patch("triage.classify", return_value=flaky_result), \
         mock.patch("darwin_harness.diagnose_via_anthropic") as mock_anthropic, \
         mock.patch("darwin_harness.diagnose_via_gemini") as mock_gemini:
        result = darwin_harness.diagnose_and_fix("x = 1", "TimeoutError: upstream slow")

    assert result is None, "diagnose_and_fix must return None for FLAKY label"
    mock_anthropic.assert_not_called()
    mock_gemini.assert_not_called()
