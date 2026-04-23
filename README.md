# Darwin

**AST-level structural patching for failing Python agents.** Deterministic LibCST transformers keyed on traceback fingerprints. Bounded blast radius. Vendor-neutral. MIT.

> Not durable execution (Agentspan, DuraLang, Temporal). Not reasoning verification (Rubric). Not retry-with-backoff. Darwin emits a **surgical AST patch** on cache hit — zero LLM calls, deterministic, auditable.

> **Now live:** [Darwin Commons](https://github.com/Miles0sage/darwin-commons) — the first public corpus of agent failure → LibCST transformer pairs. Contribute by POSTing to `/darwin/heal/public` with `publish_to_commons=true` and get a contributor badge for your README.

[![demo](https://asciinema.org/a/vsXu6gddko4rZPgM.svg)](https://asciinema.org/a/vsXu6gddko4rZPgM)

```bash
git clone https://github.com/Miles0sage/darwin && cd darwin && python3 xrepo_proof.py
```

That runs the "same bug, three different repos, one cached transformer, zero LLM calls" proof in ~3 seconds. If it prints `CROSS-REPO TRANSFER: DETERMINISTIC, REAL, AUDITABLE` you have seen the primitive. Everything below is context for what you just watched.

## Numbers (reproducible from `benchmarks/`)

| Corpus | Healed | Notes | `results.json` |
|---|---|---|---|
| **v3 strict real bugs** | **50 / 50 (100%)** | 34 Gemini Flash + 16 Opus rescues | [`benchmarks/v3/results.json`](benchmarks/v3/results.json) |
| **v2 complex real bugs** | **131 / 171 (77%)** | Gemini rate-limited, Pro+Opus carried; $1.09 total LLM spend | [`benchmarks/v2/results.json`](benchmarks/v2/results.json) |
| v1 runnable real bugs | 17 / 18 (94%)* | *17/30 in raw JSON — 12 entries had `no_reproducer` (traceback-only) and were skipped | [`benchmarks/v1/results.json`](benchmarks/v1/results.json) |
| Controlled Opus vs Gemini Flash matrix | Opus 12/12 · Gemini 2/12 | same 12 synthetic bugs across both | — |

Corpora are harvested from public GitHub issues + StackOverflow (Apache / MIT / BSD / CC BY-SA). Every bug JSON includes a source URL and license.

**Public unit tests:** `pytest` → **15/15** in ~1.6s on fresh clone (Python 3.11+).
**Status:** beta · 1 contributor · 0 paying users · MIT · Looking for EU design partners.

---

## Tagline

**Production telemetry → traceback fingerprint → cached LibCST transformer → deterministic cross-repo patch.**

## What Darwin is

Darwin is an outer-loop production reliability layer for agent fleets. A failing agent emits a traceback to a webhook; Darwin fingerprints the traceback, looks up a cached LibCST transformer keyed on that fingerprint, applies it to the concrete source of the failing repo, and gates the result through an AST safety check before writing it back. On cache hit the patch is deterministic and LLM-free; on cache miss an LLM is called exactly once to produce a transformer, which is then cached for every future occurrence of the same failure class.

## What Darwin is NOT

- **NOT** a dev-time IDE assistant. Inner-loop tools (Aider, Cursor, Devin, SWE-agent) fire while a human edits. Darwin fires after deploy, from production telemetry.
- **NOT** a chat agent, not a code-completion sidekick, not a pair-programmer. There is no conversational surface.
- **NOT** a "dataset moat" play. The cache is an optimization, not the product. Kill the cache and the primitive (AST-gated speculative repair on fingerprint match) still stands.
- **NOT** a sandbox. The transformer exec namespace is isolation, not security (see Limitations).
- **NOT** a replacement for Sentry/Datadog/OpsGenie. It consumes their webhooks; it does not replace them.

## Quickstart

```bash
# 1. Run the cross-repo transfer proof (no external services, ~3s)
python3 xrepo_proof.py

# 2. Start the production webhook (Flask, port 7777)
python3 webhook_ingest.py

# 3. Fire a production-shaped failure at it
curl -X POST http://127.0.0.1:7777/darwin/failure \
  -H 'content-type: application/json' \
  -d @examples/sentry_payload.json
```

No API keys needed for the proof — it uses a reference LibCST transformer. The webhook's B-path (cache miss → diagnose) calls out to Opus or Gemini Flash only when `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` is set; otherwise it falls back to a heuristic adapter.

## The primitive (four parts)

### 1. Fingerprint (`signature.py`)

A traceback is normalized — paths stripped to basenames, line numbers erased, memory addresses erased, hex IDs erased, `/tmp/xyz-<hash>/` temp dirs collapsed, user home paths (`/home/*/`, `/Users/*/`) masked — then SHA-256 hashed to a 64-char fingerprint. Two agents crashing with `KeyError: 'text'` at the same call-site class collide on the same hash even if one lives in `/home/alice/repo-a/agent.py:17` and the other in `/srv/repo-b/worker.py:203`. The fingerprint is the cache key; the normalized signature is kept alongside for FTS5-style lookup and for human diagnosis when fingerprints near-miss. Cross-repo fingerprint memoization is what makes cache hits possible across codebases that share a failure class but not a single line of code. See `signature.fingerprint()` and `signature.error_class()`.

### 2. CST recipe (`patch.py`)

Instead of caching a regex, a unified diff, or literal source, Darwin caches a **LibCST `CSTTransformer` source string**. The transformer encodes the structural rewrite — "when you see a `Subscript` whose value is a `Name` bound in a for-loop over an iterable of dicts and whose slice is the string literal `"text"`, replace it with a guarded `d.get("text") or d.get("data", {}).get("text")` fallback." On a cache hit Darwin loads the transformer source into an isolated Python namespace via `compile_transformer()` (restricted builtin set, no `open`, no `subprocess`, no `__import__`), parses the new (different) source to a CST, and applies the transformer deterministically. No LLM call at apply-time. If the transformer's visitor methods don't match any node in the new source ("pattern miss"), Darwin falls through to the LLM B-path with the cached diagnosis as additional context — so a near-miss degrades to a single LLM call, not a silent failure. See `PatchRecipe`, `try_apply`, and `reference_recipe_for()` for the four seeded failure classes.

### 3. AST gate (`darwin_harness.validate_fix`)

Every candidate patch — cache-hit or LLM-diagnosed — is piped through an AST comparison that rejects transformations which drop `try/except` handlers, broaden exception clauses (e.g. `except ValueError` → `except Exception`), remove `assert` statements, delete function calls on verified paths, replace raise-sites with silent `pass`, or swallow errors via `except: pass`. The gate compares the pre-patch AST against the post-patch AST and enforces an error-handling-non-weakening invariant. This is the primitive no competitor ships: AgentRx, LangChain response-cache, Copilot Autofix, Aider, and Sweep all accept whatever the LLM emits. The gate is fail-closed — a rejected patch is logged to `blackboard/rejected/` with the specific rejection reasons (`gate_reasons`) and never applied. See `validate_fix` and `log_rejected`.

### 4. fcntl fleet race (`blackboard.py`)

Multiple Darwin instances writing to the same blackboard use POSIX advisory locking (`fcntl.flock`) around cache writes, with a write-then-rename atomic commit to prevent readers from seeing torn JSON. This is honest fleet safety for N ≤ ~100 processes on a single filesystem; past that, contention dominates and a proper queue (or SQLite-WAL) is warranted (see Limitations). Crucially, a writer dying mid-lock releases the advisory flock automatically (kernel cleanup on fd close), so crash-recovery is free — the next writer proceeds, no stale lockfiles. See `blackboard.write_fix` and `blackboard.lookup`.

## Cross-repo proof

`xrepo_proof.py` ships three synthetic repos with the **same** underlying bug (schema-change `KeyError` on `["text"]`) but different variable names, different function signatures, and different control flow (for-loop vs list comprehension vs single-record). One LLM-produced-or-reference transformer heals all three. Sample tail:

```
LLM calls:       0 after first diagnose (cached recipe applied deterministically)
Repos healed:    3  (alpha, beta, gamma — different var names, different structures)
Fingerprint:     a7f3…  (same hash across 3 codebases)
Gate rejections: 0  (all 3 transforms preserved try/except and assertions)
```

For production-shaped ingestion see `webhook_ingest.py` — POST a Sentry/Datadog/generic payload to `/darwin/failure` and receive `{status: "healed", new_source: "..."}` back. `/darwin/status` exposes live counters (requests, cache_hits, llm_diagnoses, gate_rejections, heals).

## Benchmark headlines

| Fleet size (N agents, same failure class) | LLM calls | Cache hits | Gate rejections | Wall-clock |
|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 2.1s |
| 10 | 1 | 9 | 0 | 3.4s |
| 30 | 1 | 29 | 0 | 5.8s |
| 100 | 1 | 99 | 0 | 17s |
| 300 | 1 | 299 | 0 | 52s |

Headline: **N-1 LLM calls avoided per novel failure class.** Methodology, raw runs, and the no-AST-gate baseline (which propagates broken patches at the same cache-hit rate) are in `docs/BENCHMARK.md`.

The no-gate baseline is the honest comparison: with the AST gate disabled, Darwin still hits the same 99% cache-hit rate — because the cache key is the fingerprint, not the patch correctness. The gate is what separates "99% cache hits" from "99% propagated broken patches". This is the single number a reviewer should demand. Run `python3 benchmark.py --disable-gate` to see it for yourself; gate-off runs emit `N_poisoned` to stdout and a side-by-side diff to `reports/gate-off-vs-on.md`.

## Provider matrix

Darwin is vendor-neutral by construction — the only LLM dependency is in `darwin_harness.diagnose_and_fix`, which reads `DARWIN_LLM_PROVIDER` (default: `anthropic`) and dispatches to one of:

| Provider | Env var | Model | Cost / diagnose | Median latency |
|---|---|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | `claude-opus-4-7` | ~$0.02 | 3.1s |
| Google | `GEMINI_API_KEY` | `gemini-2.0-flash` | ~$0.001 | 1.4s |
| Heuristic (no-key fallback) | — | built-in pattern match | $0 | 40ms |

Cache-hit path does not touch any of these — it is pure LibCST — so the provider choice matters only on the first failure in a class. The AST gate runs identically regardless of provider. If a buyer cares about "what if Anthropic changes their API" the answer is `DARWIN_LLM_PROVIDER=google` and nothing else changes.

## Related work — acknowledge and differentiate

- **Monperrus 2020 — "The Living Review on Automated Program Repair."** Decades of APR. Most assume test-suite oracles. Darwin's oracle is the production traceback, not a suite.
- **AgentRx (Mar 2026).** Runtime agent observability and failure taxonomy. Ships the telemetry half. Darwin is the transfer-the-fix half they don't have.
- **Agent Teams / Agent-RR (record-replay).** Caches agent trajectories for replay. Darwin caches **structural rewrites**, not trajectories — transferable across repos, not bound to one trace.
- **LangChain `response_cache` / prompt-cache.** Memoizes prompt→completion. Darwin memoizes traceback→transformer. Different keys, different invariants, different failure modes.
- **Copilot Autofix / Aider / Sweep / BugBuster.** Inner-loop. Dev-time. Not vendor-neutral. No AST gate. Not cross-repo.

## Honest limitations

Darwin ships with four classes of limitation called out deliberately so reviewers don't have to find them:

1. **Four failure classes seeded.** Reference transformers exist for schema-change `KeyError`, `FileNotFoundError`, rate-limit retries, and timeout/hang. Failures outside these classes fall through to the LLM B-path and do not benefit from cross-repo transfer until a transformer is accepted.
2. **Python only.** LibCST is Python-specific. A Go/TS port would need tree-sitter plus a typed-AST comparison pass; estimated ~2 weeks per language, not shipped.
3. **Namespace isolation is NOT a security sandbox.** `patch.py:compile_transformer` restricts builtins but does not block `type.__subclasses__()` escapes, `__import__` walks on captured frames, or attribute traversal from any exposed class. Darwin trusts that (a) cache writes come only from its own diagnose path, (b) recipes are human-reviewed before import from a foreign blackboard. **Do not load third-party recipes without review.** See the SECURITY DISCLAIMER at the top of `patch.py`.
4. **Cache economics depend on accumulation.** At N=1 agent and N=1 failure class, Darwin is a slower LLM wrapper. Value shows up at N≥10 agents hitting the same class, which is why the pitch targets fleet operators (Ramp Labs, Factory.ai, Mendral) not single-repo developers. Published cache-hit-rate curve vs fleet size is the honest accounting — no "30-60x speedup" handwaving.

Additional known issues: (a) `fcntl.flock` is POSIX advisory; does not protect against non-cooperating writers. (b) Fingerprint collisions on pathologically similar tracebacks from genuinely different bugs are possible — Darwin mitigates via the AST gate (a wrong-but-cached transformer fails the gate on the wrong source). (c) `ProcessPoolExecutor` fleet at N=300 with shared-FS blackboard works; beyond that a queue or SQLite-WAL journal is warranted.

## How to use it right now

Three concrete scenarios where Darwin plugs in today:

**1. Claude Code fleet.** You run N Claude Code agents in parallel worktrees. One hits a schema-drift `KeyError` on a recent API change. It emits the traceback to `localhost:7777/darwin/failure`. Darwin diagnoses once, caches. The other N-1 agents that hit the same schema drift during their own runs cache-hit and heal locally in ~50ms each. No shared memory, no coordination — just the blackboard.

**2. Sentry webhook.** Point your Sentry (or Datadog APM, or Rollbar) alert webhook at `POST /darwin/failure` with the source file attached. Darwin responds with a healed source string and a fingerprint. Your CD pipeline opens a PR using the returned diff. The AST gate is your last-line defense against an LLM that wants to catch-all-exceptions-to-shut-the-alert-up.

**3. CI agent fleet.** Mendral-shaped: you ingest millions of CI log lines, diagnose via LLM, open PRs. Drop Darwin in post-diagnosis. A cached transformer applies to every customer repo that hits the same failure class — your per-incident LLM cost drops from O(N) to O(1).

## Apr 27 Anthropic hackathon context

Darwin was built for the Anthropic Opus 4.7 hackathon (late-apply track, 2026-04-20 submission). The primitive is vendor-neutral — the diagnose adapter accepts Claude Opus or Gemini Flash via env var, same flow, same AST gate, same cache. The repo is public, the benchmark is reproducible from a fresh clone, and the `xrepo_proof.py` run costs zero API dollars because the seeded transformer path avoids LLM calls entirely. Hackathon win-probability is a function of how many reviewers run the one-line quickstart and watch three repos heal. That is the entire pitch.

## Architecture at a glance

```
  Production agent crash
         │
         ▼
  ┌──────────────────────┐
  │  traceback (stderr)  │
  └──────────┬───────────┘
             │
             ▼
  ┌──────────────────────┐           ┌─────────────────────────┐
  │  signature.py        │──────────▶│  blackboard.py          │
  │  normalize → SHA-256 │   lookup  │  fcntl-locked JSON      │
  └──────────┬───────────┘           └──────────┬──────────────┘
             │ fingerprint                      │
             │                           hit ◄──┤──► miss
             │                                  │      │
             ▼                                  │      ▼
      (cache hit path)                          │  LLM diagnose
             │                                  │  (Opus/Gemini)
             ▼                                  │      │
  ┌──────────────────────┐                      │      │
  │  patch.py try_apply  │                      │      │
  │  LibCST transformer  │                      │      │
  └──────────┬───────────┘                      │      │
             │                                  │      │
             ▼                                  │      ▼
  ┌──────────────────────┐◄─────────────────────┴──────┘
  │  validate_fix        │  AST gate (fail-closed)
  │  error-handling      │
  │  non-weakening       │
  └──────────┬───────────┘
             │ passed
             ▼
   healed source returned
```

---

## File layout

| Path | Role |
|---|---|
| `signature.py` | Fingerprint normalizer (`fingerprint`, `error_class`) |
| `patch.py` | `PatchRecipe`, `compile_transformer`, `try_apply`, `reference_recipe_for` |
| `darwin_harness.py` | `validate_fix` (AST gate), `diagnose_and_fix` (LLM B-path) |
| `blackboard.py` | fcntl-locked JSON fix store (`write_fix`, `lookup`, `log_rejected`) |
| `webhook_ingest.py` | Flask endpoint + live counters (`/darwin/failure`, `/darwin/status`, `/darwin/fixes`) |
| `xrepo_proof.py` | The 3-repo cross-repo transfer demo |
| `benchmark.py` | Fleet-size scaling benchmark + `--disable-gate` baseline |
| `docs/BENCHMARK.md` | Methodology, raw runs, no-gate baseline, cache-hit curve |
| `examples/` | Sentry / Datadog / generic stack-trace payloads for smoke tests |

## License and contribution

MIT. PRs welcome — the four seeded failure classes are deliberately a starter set. Adding a fifth means: (1) write a reference `CSTTransformer` in `patch.py::reference_recipe_for`, (2) add a pristine + expected-fix pair under `pristines/`, (3) extend `benchmark.py` to include the new class, (4) re-run the no-gate baseline to verify the AST gate rejects plausible wrong-patches for your class. No framework lock-in — if you can write a LibCST `CSTTransformer`, you can contribute a recipe.

---

## Honesty caveats (April 22, 2026)

- **Beta, 1 contributor, 0 paying users.** Darwin has not been deployed to a production tenant yet.
- **Benchmark numbers cite `benchmarks/v*/results.json`** — clone, inspect, re-run. Source URLs on every bug.
- **Public test suite: 15 tests** in `test_crossfeed.py` + `test_triage.py`. The Hermes integration (`darwin_fleet_dashboard_tool.py`, `darwin_postmortem_tool.py`, `darwin_heal_tool.py` + their tests) lives in a separate upstream PR to Nous Research and is not bundled here.
- **Cross-repo crossfeed** has been demonstrated in-process across three synthetic repos (`xrepo_proof.py`). Two-machine over-HTTP demo is pending.
- **No head-to-head benchmark** yet against LangChain Open SWE, Self-Healing-SRE-Agent, Helix, or Microsoft AgentRx. Planned.
- **"AST-level structural patching with bounded blast radius"** — this is deliberately NOT "self-heal" (term is tainted) and NOT "durable execution" (owned by Agentspan/DuraLang/Temporal — different primitive).
