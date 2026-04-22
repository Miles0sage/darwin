#!/usr/bin/env python3
"""
Darwin benchmark runner — evaluates the harness against real-bug corpora.

Usage:
    python benchmarks/run.py --corpus v3
    python benchmarks/run.py --corpus v2
    python benchmarks/run.py --corpus all
    python benchmarks/run.py --corpus v3 --no-cache
    python benchmarks/run.py --corpus v3 --timeout 120

No LLM keys required for cache-hit path. Set GEMINI_API_KEY or
ANTHROPIC_API_KEY for cache-miss (LLM diagnose) path.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from darwin_harness import diagnose_and_fix, validate_fix
from blackboard import Blackboard
from signature import fingerprint

CORPUS_DIR = Path(__file__).parent

CORPORA = {
    "v1": {
        "dir": CORPUS_DIR / "v1",
        "pattern": "bug_*.json",
        "results": "real-bugs-results.json",
    },
    "v2": {
        "dir": CORPUS_DIR / "v2",
        "pattern": "bug_*.json",
        "results": "real-bugs-v2big-r2-results.json",
    },
    "v3": {
        "dir": CORPUS_DIR / "v3",
        "pattern": "bug_*.json",
        "results": "real-bugs-v3-results.json",
    },
}


def run_corpus(corpus_name: str, timeout: int, no_cache: bool) -> dict:
    """Run a single corpus and return summary stats."""
    cfg = CORPORA[corpus_name]
    bugs = sorted(cfg["dir"].glob(cfg["pattern"]))
    if not bugs:
        print(f"  [WARN] No bug files found in {cfg['dir']}")
        return {}

    bb = Blackboard(clear=no_cache)
    results = []
    healed = 0
    skipped = 0
    failed = 0

    print(f"\n=== Corpus {corpus_name} — {len(bugs)} bugs ===")

    for bug_path in bugs:
        with open(bug_path) as f:
            bug = json.load(f)

        bug_id = bug.get("id", bug_path.stem)
        source_code = bug.get("source_code", "")
        stderr = bug.get("stderr", "")

        if not source_code or not stderr:
            print(f"  SKIP  {bug_id} (missing source_code or stderr)")
            skipped += 1
            results.append({"id": bug_id, "status": "skipped", "reason": "no_source"})
            continue

        t0 = time.time()
        try:
            result = diagnose_and_fix(
                source_code=source_code,
                stderr=stderr,
                blackboard=bb,
                timeout=timeout,
            )
            elapsed = time.time() - t0

            if result and result.get("fixed_source"):
                gate_ok = validate_fix(source_code, result["fixed_source"])
                if gate_ok:
                    healed += 1
                    status = "healed"
                    print(f"  HEALED {bug_id} ({elapsed:.1f}s, {result.get('provider', 'cache')})")
                else:
                    failed += 1
                    status = "gate_rejected"
                    print(f"  REJECT {bug_id} — AST gate failed")
            else:
                failed += 1
                status = "no_fix"
                print(f"  MISS   {bug_id} ({elapsed:.1f}s)")

            results.append({
                "id": bug_id,
                "status": status,
                "elapsed_s": round(elapsed, 2),
                "provider": result.get("provider") if result else None,
            })

        except Exception as e:
            elapsed = time.time() - t0
            failed += 1
            print(f"  ERROR  {bug_id} — {e}")
            results.append({"id": bug_id, "status": "error", "error": str(e), "elapsed_s": round(elapsed, 2)})

    attempted = healed + failed
    rate = healed / attempted * 100 if attempted else 0
    summary = {
        "corpus": corpus_name,
        "total": len(bugs),
        "skipped": skipped,
        "attempted": attempted,
        "healed": healed,
        "failed": failed,
        "heal_rate_pct": round(rate, 1),
        "results": results,
    }

    print(f"\n  {corpus_name}: {healed}/{attempted} healed = {rate:.0f}%  ({skipped} skipped)")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Darwin benchmark runner")
    parser.add_argument("--corpus", choices=["v1", "v2", "v3", "all"], required=True)
    parser.add_argument("--timeout", type=int, default=90, help="Per-bug timeout in seconds")
    parser.add_argument("--no-cache", action="store_true", help="Disable blackboard cache")
    args = parser.parse_args()

    corpora = list(CORPORA.keys()) if args.corpus == "all" else [args.corpus]
    all_summaries = []

    for name in corpora:
        summary = run_corpus(name, args.timeout, args.no_cache)
        all_summaries.append(summary)

    print("\n=== Final Summary ===")
    total_healed = sum(s.get("healed", 0) for s in all_summaries)
    total_attempted = sum(s.get("attempted", 0) for s in all_summaries)
    for s in all_summaries:
        print(f"  {s['corpus']}: {s['healed']}/{s['attempted']} = {s['heal_rate_pct']}%")
    if len(all_summaries) > 1:
        overall = total_healed / total_attempted * 100 if total_attempted else 0
        print(f"  OVERALL: {total_healed}/{total_attempted} = {overall:.0f}%")


if __name__ == "__main__":
    main()
