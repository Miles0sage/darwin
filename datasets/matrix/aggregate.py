"""Aggregate matrix.jsonl into summary.json + unique_heals.jsonl + disagreements.jsonl."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

OUT = Path("/root/claude-code-agentic/darwin-mvp/datasets/matrix")
MATRIX = OUT / "matrix.jsonl"
SUMMARY = OUT / "summary.json"
UNIQUE = OUT / "unique_heals.jsonl"
DISAG = OUT / "disagreements.jsonl"


def normalize_cluster_key(stderr_excerpt: str) -> str:
    s = stderr_excerpt[:200]
    # strip filesystem paths
    s = re.sub(r"/[\w\-\./]+\.py", "<path>", s)
    s = re.sub(r'"[^"]*\.py"', '"<path>"', s)
    # strip line numbers
    s = re.sub(r"line \d+", "line N", s)
    # strip hex IDs and uuids
    s = re.sub(r"0x[0-9a-fA-F]{4,}", "0xHEX", s)
    s = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", s)
    # collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s[:150]


def main() -> None:
    rows = [json.loads(l) for l in MATRIX.read_text().splitlines() if l.strip()]

    # Group by bug_id
    by_bug: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_bug[r["bug_id"]].append(r)

    # Per-provider heal rate
    provider_total: dict[str, int] = defaultdict(int)
    provider_healed: dict[str, int] = defaultdict(int)
    provider_errors: dict[str, int] = defaultdict(int)
    provider_lat_total: dict[str, int] = defaultdict(int)
    for r in rows:
        provider_total[r["provider"]] += 1
        if r["healed"]:
            provider_healed[r["provider"]] += 1
        if r.get("error_in_heal"):
            provider_errors[r["provider"]] += 1
        provider_lat_total[r["provider"]] += r.get("latency_ms", 0)

    heal_rate = {
        p: {
            "attempted": provider_total[p],
            "healed": provider_healed[p],
            "rate": provider_healed[p] / provider_total[p] if provider_total[p] else 0,
            "errors": provider_errors[p],
            "avg_latency_ms": provider_lat_total[p] // max(1, provider_total[p]),
        }
        for p in provider_total
    }

    # Agreement matrix (% of bugs where N providers agree on healed/not)
    agreement_counts = defaultdict(int)  # n_healed → bug count
    for bug_id, group in by_bug.items():
        n_healed = sum(1 for g in group if g["healed"])
        agreement_counts[n_healed] += 1

    # Unique heals: bugs where exactly 1 provider succeeded
    unique_heals = []
    for bug_id, group in by_bug.items():
        healed = [g for g in group if g["healed"]]
        if len(healed) == 1:
            r = healed[0]
            unique_heals.append({
                "bug_id": bug_id,
                "winner": r["provider"],
                "error_class": r["error_class"],
                "patch_len": r["patch_len"],
                "patch_first_line": (r["patch_diff"] or "").splitlines()[0][:120] if r["patch_diff"] else "",
                "all_results": [
                    {
                        "provider": g["provider"],
                        "healed": g["healed"],
                        "err": g.get("error_in_heal"),
                    }
                    for g in group
                ],
            })

    # Disagreements: bugs with >=2 healed but patch contents differ semantically
    disagreements = []
    for bug_id, group in by_bug.items():
        healed = [g for g in group if g["healed"]]
        if len(healed) < 2:
            continue
        # Compare via length-bucket + first/last line signature
        sigs = []
        for g in healed:
            patch = g["patch_diff"] or ""
            lines = patch.splitlines()
            first = lines[0][:80] if lines else ""
            last = lines[-1][:80] if lines else ""
            sigs.append({
                "provider": g["provider"],
                "patch_len": g["patch_len"],
                "first": first,
                "last": last,
                "patch_excerpt": patch[:400],
            })
        # different if any pair differs in (len_bucket, first, last)
        unique_sigs = {(s["patch_len"] // 50, s["first"], s["last"]) for s in sigs}
        if len(unique_sigs) >= 2:
            disagreements.append({
                "bug_id": bug_id,
                "error_class": healed[0]["error_class"],
                "n_providers_healed": len(healed),
                "signatures": sigs,
            })

    # Cluster-level breakdown — group bugs by normalized error excerpt
    cluster_map: dict[str, list[str]] = defaultdict(list)
    bug_clusters: dict[str, str] = {}
    # We need the original error excerpt — pull from corpus
    corpus_path = Path("/root/claude-code-agentic/darwin-mvp/datasets/github-failures/langchain-ai-langgraph.jsonl")
    with corpus_path.open() as f:
        for line in f:
            d = json.loads(line)
            key = normalize_cluster_key(d.get("error_excerpt") or "")
            cluster_map[key].append(d["id"])
            bug_clusters[d["id"]] = key

    cluster_stats = {}
    for cluster, bug_ids in cluster_map.items():
        if len(bug_ids) < 1:
            continue
        per_provider = defaultdict(lambda: {"attempted": 0, "healed": 0})
        for bug_id in bug_ids:
            for r in by_bug.get(bug_id, []):
                per_provider[r["provider"]]["attempted"] += 1
                if r["healed"]:
                    per_provider[r["provider"]]["healed"] += 1
        cluster_stats[cluster] = {
            "n_bugs": len(bug_ids),
            "per_provider": {
                p: {
                    "attempted": v["attempted"],
                    "healed": v["healed"],
                    "rate": v["healed"] / v["attempted"] if v["attempted"] else 0,
                }
                for p, v in per_provider.items()
            },
        }

    summary = {
        "total_bugs": len(by_bug),
        "total_rows": len(rows),
        "heal_rate_per_provider": heal_rate,
        "agreement_distribution": {
            f"{k}_providers_healed": v for k, v in sorted(agreement_counts.items())
        },
        "n_unique_heals": len(unique_heals),
        "n_disagreements": len(disagreements),
        "clusters": cluster_stats,
    }

    SUMMARY.write_text(json.dumps(summary, indent=2))
    with UNIQUE.open("w") as f:
        for u in unique_heals:
            f.write(json.dumps(u) + "\n")
    with DISAG.open("w") as f:
        for d in disagreements:
            f.write(json.dumps(d) + "\n")

    print("=== HEADLINE ===")
    print(f"bugs={len(by_bug)} rows={len(rows)} unique={len(unique_heals)} disagree={len(disagreements)}")
    for p, st in heal_rate.items():
        print(f"  {p:>10}: {st['healed']}/{st['attempted']} = {st['rate']*100:.1f}% (errs={st['errors']}, avg_lat={st['avg_latency_ms']}ms)")
    print(f"\nAgreement: {dict(sorted(agreement_counts.items()))}")
    print(f"\nFiles: {SUMMARY.name} {UNIQUE.name} {DISAG.name}")


if __name__ == "__main__":
    main()
