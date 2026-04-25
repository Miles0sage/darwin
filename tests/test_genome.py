"""Tests for the Healing Genome primitive."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from genome import (  # noqa: E402
    EXTINCTION_THRESHOLD,
    EXTINCTION_WINDOW,
    GENESIS_HASH,
    Genome,
    GenomeError,
    HealPatch,
    workload_signature,
)


# ---------- helpers ----------------------------------------------------------


def _make_patch(
    *,
    sk: Ed25519PrivateKey,
    parent: str,
    failure_sig: str = "f" * 64,
    diff: str = "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n",
    score: float = 0.95,
    timestamp: datetime | None = None,
) -> HealPatch:
    ts = (timestamp or datetime.now(timezone.utc)).isoformat()
    return HealPatch.sign(
        private_key=sk,
        failure_signature=failure_sig,
        patch_diff=diff,
        parent_genome_hash=parent,
        verifier_score=score,
        timestamp_utc=ts,
    )


# ---------- sign + verify round trip ----------------------------------------


def test_sign_and_verify_round_trip():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    p = _make_patch(sk=sk, parent=g.head_hash())
    assert p.verify_signature() is True
    g.append(p)
    assert len(g.patches) == 1
    assert g.head_hash() != GENESIS_HASH


def test_serialize_deserialize_round_trip():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    g.append(_make_patch(sk=sk, parent=g.head_hash(), failure_sig="a" * 64))
    g.append(_make_patch(sk=sk, parent=g.head_hash(), failure_sig="b" * 64))

    blob = g.serialize()
    g2 = Genome.deserialize(blob)
    assert g2.head_hash() == g.head_hash()
    assert all(p.verify_signature() for p in g2.patches)


def test_failure_signature_helper_is_deterministic():
    a = HealPatch.build_failure_signature("trace", "langchain", "prompt-X")
    b = HealPatch.build_failure_signature("trace", "langchain", "prompt-X")
    c = HealPatch.build_failure_signature("trace", "langchain", "prompt-Y")
    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex


# ---------- tampered patch rejected -----------------------------------------


def test_tampered_diff_rejected():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    p = _make_patch(sk=sk, parent=g.head_hash())
    p.patch_diff = p.patch_diff + "MALICIOUS"
    assert p.verify_signature() is False
    with pytest.raises(GenomeError, match="signature invalid"):
        g.append(p)


def test_tampered_score_rejected():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    p = _make_patch(sk=sk, parent=g.head_hash(), score=0.5)
    p.verifier_score = 0.99  # forge a higher score post-sign
    assert p.verify_signature() is False


def test_tampered_signer_rejected():
    sk = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    g = Genome()
    p = _make_patch(sk=sk, parent=g.head_hash())
    # swap pubkey to someone else's — sig was over the original pubkey
    p.signer_pubkey = (
        other.public_key()
        .public_bytes(
            encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.Raw,
            format=__import__("cryptography").hazmat.primitives.serialization.PublicFormat.Raw,
        )
        .hex()
    )
    assert p.verify_signature() is False


def test_invalid_score_at_sign_time_rejected():
    sk = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError):
        HealPatch.sign(
            private_key=sk,
            failure_signature="f" * 64,
            patch_diff="diff",
            parent_genome_hash=GENESIS_HASH,
            verifier_score=1.5,
        )


# ---------- parent-hash mismatch rejected -----------------------------------


def test_parent_hash_mismatch_rejected():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    # claim parent = wrong hash
    bad = _make_patch(sk=sk, parent="deadbeef" * 8)
    with pytest.raises(GenomeError, match="parent_genome_hash mismatch"):
        g.append(bad)


def test_parent_hash_mismatch_after_first_patch():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    g.append(_make_patch(sk=sk, parent=g.head_hash()))
    # second patch points at GENESIS instead of current head
    p2 = _make_patch(sk=sk, parent=GENESIS_HASH, failure_sig="b" * 64)
    with pytest.raises(GenomeError, match="parent_genome_hash mismatch"):
        g.append(p2)


def test_head_hash_changes_with_each_append():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    seen = {g.head_hash()}
    for i in range(3):
        g.append(
            _make_patch(sk=sk, parent=g.head_hash(), failure_sig=str(i) * 64)
        )
        h = g.head_hash()
        assert h not in seen
        seen.add(h)


# ---------- extinction detection over fixture data --------------------------


def test_extinction_detection_fires_when_threshold_and_window_met():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    now = datetime.now(timezone.utc)
    old = now - EXTINCTION_WINDOW - timedelta(hours=1)

    extinct_sig = "e" * 64
    # 3 healings of the extinct class, all old
    for i in range(EXTINCTION_THRESHOLD):
        g.append(
            _make_patch(
                sk=sk,
                parent=g.head_hash(),
                failure_sig=extinct_sig,
                timestamp=old + timedelta(minutes=i),
            )
        )

    extinct = g.extinct_classes(now=now)
    assert extinct_sig in extinct


def test_extinction_skips_recent_classes():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=5)
    sig = "r" * 64

    for i in range(EXTINCTION_THRESHOLD):
        g.append(
            _make_patch(
                sk=sk,
                parent=g.head_hash(),
                failure_sig=sig,
                timestamp=recent + timedelta(seconds=i),
            )
        )

    # last seen is recent => still actively recurring => NOT extinct
    assert sig not in g.extinct_classes(now=now)


def test_extinction_skips_below_threshold():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    now = datetime.now(timezone.utc)
    old = now - EXTINCTION_WINDOW - timedelta(hours=2)
    sig = "u" * 64

    for i in range(EXTINCTION_THRESHOLD - 1):
        g.append(
            _make_patch(
                sk=sk,
                parent=g.head_hash(),
                failure_sig=sig,
                timestamp=old + timedelta(seconds=i),
            )
        )

    assert sig not in g.extinct_classes(now=now)


def test_extinction_mixed_fixture():
    sk = Ed25519PrivateKey.generate()
    g = Genome()
    now = datetime.now(timezone.utc)
    old = now - EXTINCTION_WINDOW - timedelta(hours=3)

    extinct_sig = "1" * 64
    rare_sig = "2" * 64
    recent_sig = "3" * 64

    # extinct: 3 old occurrences
    for i in range(3):
        g.append(
            _make_patch(
                sk=sk, parent=g.head_hash(), failure_sig=extinct_sig,
                timestamp=old + timedelta(seconds=i),
            )
        )
    # rare: 1 old occurrence
    g.append(
        _make_patch(sk=sk, parent=g.head_hash(), failure_sig=rare_sig, timestamp=old)
    )
    # recent: 4 fresh occurrences (above threshold but actively recurring)
    fresh = now - timedelta(minutes=10)
    for i in range(4):
        g.append(
            _make_patch(
                sk=sk, parent=g.head_hash(), failure_sig=recent_sig,
                timestamp=fresh + timedelta(seconds=i),
            )
        )

    extinct = g.extinct_classes(now=now)
    assert extinct == {extinct_sig}


# ---------- WorkloadSignature -----------------------------------------------


def test_workload_signature_is_32_bytes_and_stable():
    sig = workload_signature("langchain", ["search", "calculator"], "Solve {q}")
    assert isinstance(sig, bytes)
    assert len(sig) == 32

    again = workload_signature("langchain", ["calculator", "search"], "Solve {q}")
    assert sig == again  # tool order doesn't matter

    different = workload_signature("crewai", ["search", "calculator"], "Solve {q}")
    assert sig != different
