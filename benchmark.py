#!/usr/bin/env python3
"""
Darwin Benchmark — parallel fleet + shared flock-locked blackboard.

Day 2 upgrade (Apr 22 2026):
  - Workers run in parallel via `concurrent.futures.ProcessPoolExecutor`.
  - Each worker has its own isolated tmpdir (naive agent + broken config).
  - All workers share ONE blackboard (fixes/) — writes go through fcntl flock.
  - First worker to miss the cache computes the fix; N-1 workers read from
    cache after the lock releases.

The race is real: all N workers wake together at a barrier timestamp.

Usage:
  python3 benchmark.py --fleet-size 100
  python3 benchmark.py --fleet-size 100 --max-workers 25   # cap parallelism
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent

# scenario name → (pristine filename, config transform)
SCENARIOS: dict[str, tuple[str, bool]] = {
    # name            (pristine file,              switch_to_v2)
    "schema":         ("agent.py",                 True),
    "missing":        ("agent_missing.py",         False),
    "rate-limit":     ("agent_ratelimit.py",       False),
    "timeout":        ("agent_timeout.py",         False),
}


def _setup_worker_dir(scenario: str) -> Path:
    """Isolated tmpdir: scenario-specific naive agent + config + api/ fixtures."""
    pristine_name, switch_v2 = SCENARIOS[scenario]
    run_dir = Path(tempfile.mkdtemp(prefix="darwin-worker-"))
    shutil.copy(BASE_DIR / ".pristine" / pristine_name, run_dir / "agent.py")
    cfg = (BASE_DIR / "config.yaml").read_text()
    if switch_v2:
        cfg = cfg.replace("api_version: v1", "api_version: v2")
    (run_dir / "config.yaml").write_text(cfg)
    shutil.copytree(BASE_DIR / "api", run_dir / "api")
    return run_dir


def _run_agent(run_dir: Path) -> tuple[bool, str, str]:
    result = subprocess.run(
        [sys.executable, str(run_dir / "agent.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(run_dir),
    )
    return result.returncode == 0, result.stdout, result.stderr


def heal_worker(agent_id: int, barrier_time: float, scenario: str = "schema") -> dict:
    """Single agent: crash → consult blackboard → apply → verify.

    All workers sleep until `barrier_time` so the race hits the flock
    simultaneously rather than in arrival order.
    """
    import blackboard
    from darwin_harness import diagnose_and_fix, validate_fix

    delay = barrier_time - time.time()
    if delay > 0:
        time.sleep(delay)

    run_dir = _setup_worker_dir(scenario)
    agent_file = run_dir / "agent.py"

    t0 = time.perf_counter()
    ok_crash, _, stderr = _run_agent(run_dir)
    t_crash = time.perf_counter()

    if ok_crash:
        shutil.rmtree(run_dir, ignore_errors=True)
        return {
            "id": agent_id,
            "scenario": scenario,
            "crashed": False,
            "healed": True,
            "llm_called": False,
            "blackboard_hit": False,
            "ms": int((t_crash - t0) * 1000),
        }

    was_first = False
    rejected_reasons: list[str] | None = None
    prior = blackboard.lookup(stderr)
    cache_path = prior is not None  # true if lookup OR flock-recheck returned a fix

    if prior is not None:
        fix_code = prior["fix_code"]
    else:
        source = agent_file.read_text()

        def _compute() -> tuple[str | None, bool, list[str]]:
            fixed = diagnose_and_fix(source, stderr)
            if fixed is None:
                return None, False, ["diagnose returned None"]
            # --disable-gate baseline: skip the AST safety gate entirely.
            # Mirrors how naive cache+LLM systems behave without validation.
            if os.environ.get("DARWIN_DISABLE_GATE") == "1":
                return fixed, True, []
            ok_gate, reasons = validate_fix(source, fixed, stderr)
            return fixed, ok_gate, reasons

        root_cause_map = {
            "schema": "API v2 moved text to data.text",
            "missing": "hardcoded v3 path — fall back to v1 until endpoint deploys",
            "rate-limit": "upstream 429 with no backoff — insert sleep",
        }
        was_first, entry, rejected_reasons = blackboard.compute_and_write_fix(
            stderr,
            _compute,
            root_cause_map.get(scenario, "unknown"),
            f"agent-{agent_id:03d}",
        )
        # If the flock recheck found a prior fix, entry is that fix — also a cache hit.
        if entry is not None and not was_first:
            cache_path = True
        if entry is None:
            shutil.rmtree(run_dir, ignore_errors=True)
            return {
                "id": agent_id,
                "crashed": True,
                "healed": False,
                "llm_called": was_first,
                "blackboard_hit": False,
                "rejected_reasons": rejected_reasons,
                "ms": int((time.perf_counter() - t0) * 1000),
            }
        fix_code = entry["fix_code"]

    agent_file.write_text(fix_code)
    ok2, _, _ = _run_agent(run_dir)
    t_end = time.perf_counter()

    shutil.rmtree(run_dir, ignore_errors=True)

    return {
        "id": agent_id,
        "scenario": scenario,
        "crashed": True,
        "healed": ok2,
        "llm_called": was_first,
        "blackboard_hit": cache_path,
        "ms": int((t_end - t0) * 1000),
        "crash_ms": int((t_crash - t0) * 1000),
    }


def reset_blackboard() -> None:
    import blackboard

    if blackboard.FIXES_DIR.exists():
        for p in blackboard.FIXES_DIR.glob("fix-*.json"):
            p.unlink()
    rej = blackboard.FIXES_DIR / "rejected"
    if rej.exists():
        for p in rej.glob("rejected-*.json"):
            p.unlink()


def _run_one_scenario(
    scenario: str, n: int, workers: int, barrier_delay: float
) -> tuple[list[dict], int]:
    """Run a single scenario against a fresh fleet. Returns (results, wall_ms)."""
    print(f"\n{'─' * 60}")
    print(f"  Scenario: {scenario}   (fleet={n}, workers={workers})")
    print(f"{'─' * 60}")
    barrier_time = time.time() + barrier_delay
    t_wall0 = time.perf_counter()
    results: list[dict] = []
    with cf.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(heal_worker, i, barrier_time, scenario) for i in range(1, n + 1)
        ]
        for f in cf.as_completed(futures):
            r = f.result()
            results.append(r)
            tag = (
                "LLM " if r.get("llm_called")
                else ("CACHE" if r.get("blackboard_hit") else "MISS ")
            )
            status = "OK" if r["healed"] else "FAIL"
            print(f"  [{scenario:>10}] agent-{r['id']:03d}  [{tag}]  {r.get('ms', 0):>5} ms  {status}")
    wall_ms = int((time.perf_counter() - t_wall0) * 1000)
    return results, wall_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fleet-size", type=int, default=10)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="cap parallelism (default = fleet-size, true 'all at once')",
    )
    parser.add_argument(
        "--scenario",
        default="schema",
        choices=["schema", "missing", "rate-limit", "timeout", "all"],
        help="failure class to inject (default: schema). 'all' runs all back-to-back.",
    )
    parser.add_argument(
        "--disable-gate",
        action="store_true",
        help="TURN OFF the AST safety gate. Baseline run — poisoned fixes propagate unchecked.",
    )
    parser.add_argument(
        "--keep-blackboard",
        action="store_true",
        help="don't clear blackboard before run",
    )
    parser.add_argument(
        "--barrier-delay",
        type=float,
        default=1.0,
        help="seconds to wait at barrier so workers fire together (default: 1.0)",
    )
    args = parser.parse_args()
    n = args.fleet_size
    workers = args.max_workers or n

    scenarios = (
        ["schema", "missing", "rate-limit", "timeout"] if args.scenario == "all" else [args.scenario]
    )

    if args.disable_gate:
        os.environ["DARWIN_DISABLE_GATE"] = "1"

    print(f"\n{'=' * 60}")
    print(f"  DARWIN BENCHMARK — parallel fleet")
    print(f"  Scenarios: {', '.join(scenarios)}")
    print(f"  Fleet per scenario: {n}  (max_workers={workers})")
    print(f"  Blackboard: {Path(os.environ.get('DARWIN_FIXES_DIR', BASE_DIR / 'fixes'))}")
    print(f"{'=' * 60}")

    if not args.keep_blackboard:
        reset_blackboard()

    all_results: list[dict] = []
    per_scenario_wall: dict[str, int] = {}
    t_grand0 = time.perf_counter()
    for sc in scenarios:
        rs, wall_ms = _run_one_scenario(sc, n, workers, args.barrier_delay)
        all_results.extend(rs)
        per_scenario_wall[sc] = wall_ms
    t_grand1 = time.perf_counter()

    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    total_fleet = len(all_results)
    llm_calls = sum(1 for r in all_results if r.get("llm_called"))
    cache_hits = sum(1 for r in all_results if r.get("blackboard_hit"))
    healed = sum(1 for r in all_results if r["healed"])
    expected_llm = len(scenarios) if not args.keep_blackboard else 0
    expected_cache = total_fleet - expected_llm

    print(f"  Total agents:      {total_fleet}  ({len(scenarios)} scenario(s) × {n} fleet)")
    print(f"  Healed:            {healed}/{total_fleet}")
    print(f"  LLM calls:         {llm_calls}  (expected: {expected_llm})")
    print(f"  Cache hits:        {cache_hits}  (expected: {expected_cache})")
    print(f"  Rejected fixes:    {__import__('blackboard').count_rejected()}  (safety gate)")
    print(f"  Grand wall-clock:  {(t_grand1 - t_grand0) * 1000:.0f} ms")
    for sc, ms in per_scenario_wall.items():
        print(f"    {sc:>10}: {ms} ms")

    llm_times = [r.get("ms", 0) for r in all_results if r.get("llm_called")]
    cache_times = [r.get("ms", 0) for r in all_results if r.get("blackboard_hit")]
    if llm_times and cache_times:
        print(f"  LLM path avg:      {statistics.mean(llm_times):.0f} ms")
        print(f"  Cache path avg:    {statistics.mean(cache_times):.0f} ms")

    # Detect actual LLM provider used
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    if has_anthropic:
        provider = "Claude Opus 4.7"
    elif has_gemini:
        provider = f"Gemini ({os.environ.get('DARWIN_GEMINI_MODEL', 'gemini-2.5-flash')})"
    else:
        provider = "heuristic regex (no API key)"
    print(f"  Diagnose provider: {provider}")

    # Cost-amortization story — Nx LLM calls avoided, not wall-clock speedup
    avoided_calls = cache_hits
    if avoided_calls > 0 and llm_calls > 0:
        multiplier = (llm_calls + avoided_calls) / llm_calls
        print(
            f"  LLM calls avoided: {avoided_calls} (amortized 1 diagnose across"
            f" {multiplier:.0f} agents — {multiplier:.0f}x cost reduction)"
        )

    print(f"  Total CPU-time:    {sum(r.get('ms', 0) for r in all_results)} ms\n")

    report = {
        "scenarios": scenarios,
        "fleet_size_per_scenario": n,
        "max_workers": workers,
        "total_agents": total_fleet,
        "healed": healed,
        "llm_calls": llm_calls,
        "blackboard_hits": cache_hits,
        "grand_wall_clock_ms": int((t_grand1 - t_grand0) * 1000),
        "per_scenario_wall_ms": per_scenario_wall,
        "results": all_results,
        "mode": (
            "anthropic" if os.environ.get("ANTHROPIC_API_KEY")
            else ("gemini" if os.environ.get("GEMINI_API_KEY") else "heuristic")
        ),
        "provider": provider,
    }
    (BASE_DIR / "benchmark-report.json").write_text(json.dumps(report, indent=2))
    print(f"  Report → benchmark-report.json\n")


if __name__ == "__main__":
    main()
