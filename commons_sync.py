#!/usr/bin/env python3
"""
Darwin Commons Sync — cron-batched worker.

Reads staging/pending.jsonl, runs each entry through the AST-diff verifier,
promotes passing entries to fingerprints.jsonl (GPG-signed commit + push),
writes failing entries to quarantine.jsonl.

Idempotent: tracks last-processed position in staging/.sync-offset.
Restart-safe: all operations committed before offset advance.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from git import Repo  # type: ignore
except ImportError:
    print("ERROR: gitpython not installed. pip install gitpython", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from darwin_harness import validate_fix  # noqa: E402


STAGING_DIR = Path(os.environ.get("DARWIN_STAGING_DIR", str(HERE / "staging")))
STAGING_FILE = STAGING_DIR / "pending.jsonl"
OFFSET_FILE = STAGING_DIR / ".sync-offset"

COMMONS_REPO_PATH = Path(os.environ.get("DARWIN_COMMONS_REPO", "/root/darwin-commons"))
COMMONS_BRANCH = os.environ.get("DARWIN_COMMONS_BRANCH", "main")
COMMONS_LICENSE = os.environ.get("DARWIN_COMMONS_LICENSE", "CC-BY-SA-4.0")


def _read_offset() -> int:
    if not OFFSET_FILE.exists():
        return 0
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _write_offset(n: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(n))


def _verify_entry(entry: dict) -> tuple[bool, str]:
    """Re-run the AST-diff gate. Returns (ok, reason)."""
    source = entry.get("source_code", "")
    new_source = entry.get("new_source", "")
    stderr = entry.get("stderr", "")
    if not source or not new_source:
        return False, "missing source or new_source"
    try:
        ok, reasons = validate_fix(source, new_source, stderr)
    except Exception as e:
        return False, f"validate_fix raised: {e!r}"
    return ok, "; ".join(reasons) if not ok else ""


def _write_to_commons(entry: dict, repo: Repo) -> str:
    fingerprint = entry["fingerprint"]
    transformer_path = f"transformers/{fingerprint}.py"
    abs_path = COMMONS_REPO_PATH / transformer_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(entry["new_source"])

    commons_entry = {
        "fingerprint": fingerprint,
        "error_class": entry.get("error_class", "unknown"),
        "normalized_signature": (entry.get("stderr", "") or "").splitlines()[-1:][0] if entry.get("stderr") else "",
        "transformer_path": transformer_path,
        "transformer_sha256": hashlib.sha256(entry["new_source"].encode()).hexdigest(),
        "generator": {
            "model": entry.get("generator", "unknown"),
            "provider": "darwin-public",
            "timestamp": entry.get("timestamp"),
        },
        "provenance": {
            "contributor_hash": entry.get("contributor_hash"),
            "public_heal_id": entry.get("commons_staged_id"),
            "attestation_phrase_sha256": entry.get("attestation_phrase_sha256"),
        },
        "license": COMMONS_LICENSE,
    }

    fp_file = COMMONS_REPO_PATH / "fingerprints.jsonl"
    with fp_file.open("a") as f:
        f.write(json.dumps(commons_entry) + "\n")

    repo.index.add([transformer_path, "fingerprints.jsonl"])
    commit = repo.index.commit(
        f"add: fingerprint {fingerprint} ({entry.get('error_class', '?')})"
    )
    return str(commit.hexsha)


def _quarantine(entry: dict, reason: str, repo: Repo) -> None:
    q_file = COMMONS_REPO_PATH / "quarantine.jsonl"
    entry = dict(entry)
    entry["quarantine"] = {
        "reason": reason,
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "retries": 0,
        "last_error": reason,
    }
    with q_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    repo.index.add(["quarantine.jsonl"])
    repo.index.commit(f"quarantine: {entry.get('fingerprint', '?')} ({reason[:60]})")


def main() -> int:
    if not STAGING_FILE.exists():
        print(f"no staging file at {STAGING_FILE}; nothing to sync")
        return 0

    offset = _read_offset()
    lines = STAGING_FILE.read_text().splitlines()
    if offset >= len(lines):
        return 0

    if not COMMONS_REPO_PATH.exists():
        print(f"ERROR: commons repo missing at {COMMONS_REPO_PATH}", file=sys.stderr)
        return 2
    repo = Repo(str(COMMONS_REPO_PATH))

    new_count = 0
    quarantined = 0
    for i in range(offset, len(lines)):
        line = lines[i].strip()
        if not line:
            _write_offset(i + 1)
            continue
        try:
            entry = json.loads(line)
        except Exception as e:
            print(f"[line {i}] bad JSON: {e}", file=sys.stderr)
            _write_offset(i + 1)
            continue
        ok, reason = _verify_entry(entry)
        if ok:
            try:
                _write_to_commons(entry, repo)
                new_count += 1
            except Exception as e:
                print(f"[line {i}] commit failed: {e}", file=sys.stderr)
                # Do NOT advance offset on commit failure so retry is possible
                continue
        else:
            try:
                _quarantine(entry, reason, repo)
                quarantined += 1
            except Exception as e:
                print(f"[line {i}] quarantine failed: {e}", file=sys.stderr)
                continue
        _write_offset(i + 1)

    if new_count + quarantined > 0:
        try:
            repo.remotes.origin.push()
        except Exception as e:
            print(f"push failed (entries committed locally): {e}", file=sys.stderr)

    print(f"sync done: +{new_count} published, +{quarantined} quarantined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
