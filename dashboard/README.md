# Darwin Failure Index — Dashboard (scaffold)

Static, single-file dashboard. No build step, no npm, no CDN. Pure HTML+CSS+JS.

## Run

```bash
python3 -m http.server 8080 -d dashboard
# then open http://localhost:8080/
```

## Files it loads (in this order)

The page tries each path with `fetch()` and degrades gracefully when missing:

1. `dashboard/clusters.json` — array of `{cluster_id, error_signature, count, sample_ids}`
2. `dashboard/heal_results.jsonl` — one JSON object per line:
   `{row_id, healed, patch, latency_ms, provider, error_class, framework}`
3. `dashboard/_corpus.jsonl` — one JSON object per line, used for framework breakdown
4. `dashboard/seed.json` — fallback synthetic data so the page renders today

When real `clusters.json` / `heal_results.jsonl` exist next to `index.html`, the seed
is ignored. Drop them in (or symlink from sibling tasks) to switch from scaffold
to live.

## What it renders

- **Hero** — `% of N agent failure classes healed` (derived from `heal_results.jsonl`)
- **Framework breakdown** — HTML/CSS bar chart, counts per framework
- **Cluster table** — top 20 clusters, click column headers to sort
- **Recent heals** — last 10 healed rows with collapsible `<details>` patch diffs
- **Falsifiability surface** — 5 most recent unhealed rows

## Constraints honored

- Single file (`dashboard/index.html`), no build, no external scripts
- Renders gracefully with missing data files (placeholder cards)
- Mobile-friendly via flexbox + responsive bar/grid sizing
- Dark theme by default — black bg, green accent (Darwin aesthetic)
- Read-only on `genome.py`, `darwin_harness.py`, `evo.py`

## Wiring real data

Sibling task should produce, into this directory:

```
dashboard/
  clusters.json        # JSON array
  heal_results.jsonl   # one JSON per line
  _corpus.jsonl        # one JSON per line, must include `framework` field
```

Refresh the page. The header meta switches from "seed data" to "live data".
