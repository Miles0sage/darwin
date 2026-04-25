"""Healing Genome primitive for Darwin.

A portable, signed, ordered stack of healing patches. Designed to gossip
on the Hyperspace P2P network later. v0 is local-only.

Concepts:
    HealPatch          one signed healing event (failure -> diff -> verifier score)
    Genome             ordered stack of HealPatch with Merkle-style head hash
    WorkloadSignature  32-byte similarity routing key for (framework, tools, prompt)

Wire format: JSON for canonical bytes (sorted keys, no whitespace) so signatures
stay deterministic across runtimes and gossip hops.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

GENESIS_HASH = "0" * 64
EXTINCTION_THRESHOLD = 3
EXTINCTION_WINDOW = timedelta(hours=24)


# ---------- canonical encoding ----------------------------------------------


def _canonical_json(obj: dict) -> bytes:
    """Deterministic JSON: sorted keys, compact separators, utf-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- HealPatch --------------------------------------------------------


@dataclass
class HealPatch:
    failure_signature: str          # sha256 hex of (stack + framework + prompt_template_hash)
    patch_diff: str                 # unified diff string
    parent_genome_hash: str         # head hash of genome before this patch
    signer_pubkey: str              # ed25519 pubkey, hex
    signature: str                  # ed25519 sig over canonical bytes, hex
    timestamp_utc: str              # iso8601 UTC
    verifier_score: float           # [0,1] Darwin gate confidence

    # ----- canonical view ---------------------------------------------------

    def _signed_payload(self) -> dict:
        """Fields covered by the signature (everything except `signature` itself)."""
        return {
            "failure_signature": self.failure_signature,
            "patch_diff": self.patch_diff,
            "parent_genome_hash": self.parent_genome_hash,
            "signer_pubkey": self.signer_pubkey,
            "timestamp_utc": self.timestamp_utc,
            "verifier_score": self.verifier_score,
        }

    def canonical_bytes(self) -> bytes:
        """Bytes used both for signing and for Merkle hashing."""
        return _canonical_json(self._signed_payload())

    # ----- crypto -----------------------------------------------------------

    @classmethod
    def build_failure_signature(
        cls, stack_trace: str, framework: str, prompt_template: str
    ) -> str:
        prompt_hash = _sha256_hex(prompt_template.encode("utf-8"))
        material = f"{stack_trace}\x1f{framework}\x1f{prompt_hash}".encode("utf-8")
        return _sha256_hex(material)

    @classmethod
    def sign(
        cls,
        *,
        private_key: Ed25519PrivateKey,
        failure_signature: str,
        patch_diff: str,
        parent_genome_hash: str,
        verifier_score: float,
        timestamp_utc: str | None = None,
    ) -> "HealPatch":
        if not 0.0 <= verifier_score <= 1.0:
            raise ValueError("verifier_score must be in [0,1]")
        pub_hex = (
            private_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            .hex()
        )
        ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
        unsigned = cls(
            failure_signature=failure_signature,
            patch_diff=patch_diff,
            parent_genome_hash=parent_genome_hash,
            signer_pubkey=pub_hex,
            signature="",
            timestamp_utc=ts,
            verifier_score=verifier_score,
        )
        sig = private_key.sign(unsigned.canonical_bytes()).hex()
        unsigned.signature = sig
        return unsigned

    def verify_signature(self) -> bool:
        try:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.signer_pubkey))
            pub.verify(bytes.fromhex(self.signature), self.canonical_bytes())
            return True
        except (InvalidSignature, ValueError):
            return False

    # ----- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HealPatch":
        return cls(**d)


# ---------- Genome -----------------------------------------------------------


class GenomeError(Exception):
    pass


@dataclass
class Genome:
    patches: list[HealPatch] = field(default_factory=list)

    # ----- head hash --------------------------------------------------------

    def head_hash(self) -> str:
        """Merkle-style chain: H_n = sha256(H_{n-1} || canonical(patch_n))."""
        h = GENESIS_HASH
        for p in self.patches:
            h = _sha256_hex(h.encode("utf-8") + p.canonical_bytes())
        return h

    # ----- mutation ---------------------------------------------------------

    def append(self, patch: HealPatch) -> None:
        expected_parent = self.head_hash()
        if patch.parent_genome_hash != expected_parent:
            raise GenomeError(
                f"parent_genome_hash mismatch: expected {expected_parent}, "
                f"got {patch.parent_genome_hash}"
            )
        if not patch.verify_signature():
            raise GenomeError("patch signature invalid")
        self.patches.append(patch)

    # ----- (de)serialization -----------------------------------------------

    def serialize(self) -> bytes:
        payload = {"patches": [p.to_dict() for p in self.patches]}
        return _canonical_json(payload)

    @classmethod
    def deserialize(cls, blob: bytes) -> "Genome":
        payload = json.loads(blob.decode("utf-8"))
        patches = [HealPatch.from_dict(d) for d in payload.get("patches", [])]
        return cls(patches=patches)

    # ----- analytics --------------------------------------------------------

    def extinct_classes(self, now: datetime | None = None) -> set[str]:
        """Failure signatures Darwin considers 'extinct': healed >= EXTINCTION_THRESHOLD
        times AND last seen older than EXTINCTION_WINDOW (so they aren't actively
        recurring)."""
        now = now or datetime.now(timezone.utc)
        counts: Counter[str] = Counter()
        last_seen: dict[str, datetime] = {}
        for p in self.patches:
            counts[p.failure_signature] += 1
            ts = datetime.fromisoformat(p.timestamp_utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            prev = last_seen.get(p.failure_signature)
            if prev is None or ts > prev:
                last_seen[p.failure_signature] = ts
        return {
            sig
            for sig, n in counts.items()
            if n >= EXTINCTION_THRESHOLD and (now - last_seen[sig]) > EXTINCTION_WINDOW
        }


# ---------- WorkloadSignature ------------------------------------------------


def workload_signature(
    framework_name: str,
    tool_list: Iterable[str],
    prompt_template: str,
) -> bytes:
    """Return a 32-byte signature for (framework, tools, prompt) similarity routing.

    v0 = stable sha256 over canonical inputs. Sorted tools so order-insensitive.

    # TODO: replace with embedding (e.g. nomic-embed-text) once we have a vector
    # gossip layer; for now any two genomes computing this on identical inputs
    # converge on the same 32 bytes, which is enough for exact-match routing.
    """
    canonical = _canonical_json(
        {
            "framework": framework_name,
            "tools": sorted(tool_list),
            "prompt_template": prompt_template,
        }
    )
    return hashlib.sha256(canonical).digest()


__all__ = [
    "HealPatch",
    "Genome",
    "GenomeError",
    "workload_signature",
    "GENESIS_HASH",
    "EXTINCTION_THRESHOLD",
    "EXTINCTION_WINDOW",
]
