#!/usr/bin/env python3
"""
One-shot seed: read historical fixes/*.json and write directly to darwin-commons.

Bypasses the staging/verify path because legacy fix entries don't carry
source_code/stderr for AST-gate replay. These are pre-verified by the
runs that produced them.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
FIXES_DIR = HERE / "fixes"
COMMONS = Path(os.environ.get("DARWIN_COMMONS_REPO", "/root/darwin-commons"))
LICENSE = os.environ.get("DARWIN_COMMONS_LICENSE", "CC-BY-SA-4.0")
SEED_CAP = int(os.environ.get("DARWIN_SEED_CAP", "20"))


def main() -> int:
    if not FIXES_DIR.exists():
        print(f"no fixes dir at {FIXES_DIR}", file=sys.stderr)
        return 1
    if not COMMONS.exists():
        print(f"commons repo missing at {COMMONS}", file=sys.stderr)
        return 2

    fp_file = COMMONS / "fingerprints.jsonl"
    seen: set[str] = set()
    if fp_file.exists():
        for line in fp_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                seen.add(e.get("fingerprint", ""))
            except Exception:
                continue

    (COMMONS / "transformers").mkdir(exist_ok=True)

    wrote = 0
    for p in sorted(FIXES_DIR.glob("fix-*.json")):
        if wrote >= SEED_CAP:
            break
        try:
            e = json.loads(p.read_text())
        except Exception:
            continue
        fp = e.get("fingerprint") or p.stem
        if fp in seen:
            continue
        seen.add(fp)

        fix_code = e.get("fix_code", "")
        if not fix_code:
            continue

        transformer_path = f"transformers/{fp}.py"
        (COMMONS / transformer_path).write_text(fix_code)

        entry = {
            "fingerprint": fp,
            "error_class": e.get("error_class", "unknown"),
            "normalized_signature": e.get("error_signature", ""),
            "transformer_path": transformer_path,
            "transformer_sha256": hashlib.sha256(fix_code.encode()).hexdigest(),
            "generator": {
                "model": e.get("llm_provider") or "darwin-seed",
                "provider": "darwin-historical",
                "timestamp": e.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            },
            "provenance": {
                "contributor_hash": "ch-darwin-seed",
                "public_heal_id": f"ph-seed-{p.stem}",
                "attestation_phrase_sha256": None,
                "origin_repo": "Miles0sage/darwin",
                "origin_run": e.get("originating_agent", ""),
            },
            "license": LICENSE,
            "seed": True,
        }
        with fp_file.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        wrote += 1
        print(f"seeded {fp} ({e.get('error_class', '?')})")

    print(f"\nTotal seeded: {wrote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
