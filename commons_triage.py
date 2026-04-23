#!/usr/bin/env python3
"""Triage quarantined Darwin Commons entries.

Usage:
  python3 commons_triage.py list
  python3 commons_triage.py inspect <index>
  python3 commons_triage.py rescue <index>     # move back to staging for re-verify
  python3 commons_triage.py purge <index>      # drop permanently
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

COMMONS_REPO = Path(os.environ.get("DARWIN_COMMONS_REPO", "/root/darwin-commons"))
Q_FILE = COMMONS_REPO / "quarantine.jsonl"
STAGING = Path(os.environ.get("DARWIN_STAGING_DIR", "/root/claude-code-agentic/darwin-mvp/staging"))


def _load_lines() -> list[str]:
    if not Q_FILE.exists():
        return []
    return [ln for ln in Q_FILE.read_text().splitlines() if ln.strip()]


def _write_lines(lines: list[str]) -> None:
    Q_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))


def cmd_list(args: argparse.Namespace) -> int:
    lines = _load_lines()
    if not lines:
        print("no quarantined entries")
        return 0
    for i, line in enumerate(lines):
        try:
            e = json.loads(line)
        except Exception as err:
            print(f"[{i}] <unparseable: {err}>")
            continue
        q = e.get("quarantine", {})
        print(
            f"[{i}] fp={e.get('fingerprint', '?')[:12]} "
            f"class={e.get('error_class', '?')} "
            f"reason={q.get('reason', '?')[:60]}"
        )
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    lines = _load_lines()
    if args.index < 0 or args.index >= len(lines):
        print(f"index out of range 0..{len(lines)-1}", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(lines[args.index]), indent=2))
    return 0


def cmd_rescue(args: argparse.Namespace) -> int:
    lines = _load_lines()
    if args.index < 0 or args.index >= len(lines):
        print(f"index out of range 0..{len(lines)-1}", file=sys.stderr)
        return 1
    entry = json.loads(lines[args.index])
    entry.pop("quarantine", None)
    STAGING.mkdir(parents=True, exist_ok=True)
    pending = STAGING / "pending.jsonl"
    with pending.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    del lines[args.index]
    _write_lines(lines)
    print(f"rescued index {args.index} → {pending}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    lines = _load_lines()
    if args.index < 0 or args.index >= len(lines):
        print(f"index out of range 0..{len(lines)-1}", file=sys.stderr)
        return 1
    del lines[args.index]
    _write_lines(lines)
    print(f"purged index {args.index}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").set_defaults(func=cmd_list)
    i = sp.add_parser("inspect")
    i.add_argument("index", type=int)
    i.set_defaults(func=cmd_inspect)
    r = sp.add_parser("rescue")
    r.add_argument("index", type=int)
    r.set_defaults(func=cmd_rescue)
    p = sp.add_parser("purge")
    p.add_argument("index", type=int)
    p.set_defaults(func=cmd_purge)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
