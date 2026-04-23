# Darwin Commons — 72-Hour Blitz Design (Panel-Revised)
Date: 2026-04-24
Author: Miles (via Claude Opus 4.7)
Status: Panel-revised — awaiting user approval of v2
Revisions: Incorporates Gemini 2.5 Pro + GLM teardown verdicts (see `/tmp/darwin-sync/teardown-raw/spec-review-MERGED.md`). Codex review stalled — not incorporated.

## Goal

Convert Darwin from "OSS self-heal tool with 0 stars" to "the reliability data commons for the agent ecosystem" in 72 hours. Start the failure-corpus flywheel publicly with a shippable, moderatable surface. Skip ambition that can't clear 72h.

## The one-sentence pitch

**"Every time Darwin heals a failing agent, the fingerprint→transformer pair is minted into a public, attributed data commons that other self-heal tools can read, contribute to, and benchmark against."**

## What changed vs v1

Panel (Gemini 2.5 Pro score 2/10, GLM score 3/10) unanimously flagged v1 as directionally correct but mechanically unshippable. Fixes applied:

1. **Agent Lightning plugin dropped from 72h scope.** Both reviewers: <10% MSFT merge odds in 30 days without an internal champion. Moved to month-2.
2. **Async git-push-on-heal replaced with cron-batched sync from a staging store.** Eliminates the Day-1 yak shave Gemini explicitly called "will eat the whole sprint."
3. **Dead-Letter Queue + explicit quarantine/remediation flow added** before launch. No silent drops, no graveyard.
4. **Contributor attestation gate** for publishing to Commons. Addresses license-laundering risk Gemini flagged.
5. **User→contributor conversion loop** spec'd: `X-Darwin-Commons-Credit` header + Shields.io-style README badge. Addresses GLM's "no community mechanism" critique.
6. **Ed25519 deferred.** Ship with signed git commits (GPG/Sigstore) as the trust chain — standard OSS practice. Ed25519 becomes month-2 if abuse warrants.

## Non-goals (72h window)

- No Agent Lightning plugin (deferred month-2)
- No IETF RFC, no OpenAPI document, no conformance suite
- No payment / tiered access / enterprise license
- No multi-model consensus repair, no regression-net generator
- No Ed25519 — use signed git commits instead
- No rewriting `darwin-mvp` internals; additive only

## Architecture (revised — 2 surfaces not 3)

```
+-------------------------+     +-------------------------+
|  darwin-mvp (existing)  |     |   darwin-commons (new)  |
|  webhook_ingest.py      |---->|   fingerprints.jsonl    |
|  patch.py, triage.py    |     |   transformers/*.py     |
|  (unchanged)            |     |   quarantine.jsonl      |
+-------------------------+     +-------------------------+
   |                ^
   | writes         | cron every 15min
   v                | reads staging, pushes signed commit
+-------------------------+     +-------------------------+
| staging/                |     |  commons-sync.py (new)  |
|   pending.jsonl         |<----|  idempotent, restart-   |
|   quarantine.jsonl      |     |  safe, scheduled systemd|
+-------------------------+     +-------------------------+
           ^
           | /darwin/heal/public (rate-limited, budget-capped, opt-in publish)
           v
+-------------------------+
|  public HTTP surface    |
|  (port 7777)            |
+-------------------------+
```

## Component 1 — Public heal endpoint

Adds two routes to `webhook_ingest.py`:

- `POST /darwin/heal/public`
  - Body: same JSON schema as existing `/darwin/failure`, PLUS:
    - `publish_to_commons: bool` (default `false`)
    - `contributor_attestation: string` (required if `publish_to_commons=true`). Must match the phrase: `"I have the right to submit this code under CC-BY-SA-4.0."`
  - Rate limit: 10/hr/IP (env: `DARWIN_PUBLIC_RATE_LIMIT`)
  - Payload cap: 16KB; source_code cap: 8KB
  - Traceback must parse as a Python traceback (reject malformed)
  - Anonymous path: heuristic-only, no LLM call
  - Authenticated path (`x-darwin-key` header with user's own Gemini/Anthropic key): LLM call allowed
  - Response adds `commons_staged_id` if entry was written to staging for publish
  - Response adds `X-Darwin-Commons-Credit: <contributor_hash>` header
- `GET /darwin/commons/list?limit=100&since=<timestamp>`
  - Public read of published fingerprints (no source code, only fingerprint + error class + transformer hash + provenance)
- `GET /darwin/commons/badge/<contributor_hash>`
  - Returns a Shields.io-compatible SVG: "Darwin Commons · N fingerprints"
- Budget cap: env `DARWIN_PUBLIC_DAILY_BUDGET_USD=5`, enforced via in-memory counter + file-persisted daily rollover. Over budget → 429 for LLM-path requests; heuristic still available.
- Kill switch: env `DARWIN_PUBLIC_DISABLED=1` takes the endpoint offline in one env flip.

## Component 2 — Darwin Commons data repo

Separate public GitHub repo `Miles0sage/darwin-commons`:

```
darwin-commons/
├── README.md                     ← "The public failure→transformer corpus"
├── LICENSE                       ← CC-BY-SA-4.0 (default — deferred decision, see Open Questions)
├── CONTRIBUTING.md               ← attestation required, PR flow for manual submissions
├── CODE_OF_CONDUCT.md            ← standard Contributor Covenant
├── fingerprints.jsonl            ← append-only JSONL, one entry per published transformer
├── quarantine.jsonl              ← entries that failed CI verification, triageable
├── transformers/
│   └── <fingerprint>.py          ← LibCST CSTTransformer source (only for approved entries)
├── SCHEMA.md                     ← the JSONL schema (de-facto protocol)
└── .github/workflows/
    ├── commons-verify.yml        ← CI replays every entry on fresh checkout; failed entries → quarantine.jsonl via automated PR
    └── commons-credit.yml        ← nightly aggregator that updates contributor badge counts
```

Each `fingerprints.jsonl` entry:
```json
{
  "fingerprint": "0b8ed4dc613c4688",
  "error_class": "KeyError",
  "normalized_signature": "File \"a.py\", line N, in mod\n...",
  "transformer_path": "transformers/0b8ed4dc613c4688.py",
  "transformer_sha256": "abc123...",
  "generator": {
    "model": "gemini-flash-latest",
    "provider": "google",
    "timestamp": "2026-04-24T03:12:44Z"
  },
  "provenance": {
    "contributor_hash": "ch-a1b2c3d4",
    "public_heal_id": "ph-2026-04-24-000042",
    "attestation_phrase_sha256": "hash-of-the-attestation-string-they-submitted"
  },
  "license": "CC-BY-SA-4.0"
}
```

Trust chain: git commits from `darwin-commons-bot` GitHub account, **GPG-signed** (not Ed25519). Public key committed to repo root. Standard OSS trust chain.

### Dead-Letter Queue / Quarantine flow

- `commons-sync.py` writes a staging-pending entry. Every cron tick, it attempts to run the transformer against a synthetic traceback+source pair in-process.
- **Success** → promote to `fingerprints.jsonl` + commit signed.
- **Failure** → write to `quarantine.jsonl` with `{reason, first_seen, retries, last_error}` + log to `darwin-mvp/logs/quarantine.log`. Stays there 7 days, then auto-pruned unless manually rescued.
- **Triage CLI**: `python3 darwin-mvp/commons_triage.py list|inspect <id>|rescue <id>|purge <id>` — out-of-band tool for human review.

## Component 3 — agentlightning-darwin plugin (DEFERRED)

Moved to month-2. Not in 72h scope. Stub README only: announce intent, collect interest signals via GitHub star + Discussions.

## Data flow (revised)

```
Anyone's failing Python agent
  |
  | POST /darwin/heal/public  {traceback, source, publish_to_commons=true, attestation}
  v
Darwin webhook
  |
  | fingerprint → cache lookup
  |   hit:  return cached transformer + apply
  |   miss: heuristic (anon) OR LLM synthesis (BYO-key) → AST diff gate → cache
  v
Response: patched source + commons_staged_id + X-Darwin-Commons-Credit header
  |
  | appended to staging/pending.jsonl (local disk, NOT git yet)
  v
commons-sync.py (systemd timer, every 15min)
  |
  | reads staging, runs per-entry verifier in-process
  |   pass → append to fingerprints.jsonl, git commit + push (GPG-signed)
  |   fail → write to quarantine.jsonl, log, skip
  v
darwin-commons repo (public, CC-BY-SA, CI-verified, GPG-signed)
  |
  | available forever
  v
Future requesters hit cache — possibly from a different repo
  |
  | contributor badge tracks lifetime fingerprints attributed to them
  v
Social proof loop: "Add the Darwin Commons badge to your README"
```

## Success metrics (72h)

- **100 fingerprints published** to Commons by end of Day 3 (lowered from 200 — panel said Day-1 yak-shave risk made 200 unrealistic; 100 is honest).
- **50+ darwin-mvp stars** (up from 0). Proxy for traction.
- **10+ darwin-commons stars.** Independent signal that the Commons narrative works.
- **1 external contributor** opens an issue or submits a manual CONTRIBUTING.md-flow PR to `darwin-commons`.
- **First quarantined entry** surfaced and triaged by Day 3 (proves the DLQ flow isn't a graveyard).

Not metrics: revenue, Agent Lightning PR (both deferred).

## Day-by-day plan (revised)

### Day 1 (Apr 24)
- **Morning (3h)**:
  - Implement `/darwin/heal/public` + rate limit + budget cap + attestation check in `webhook_ingest.py`
  - Add `staging/pending.jsonl` write path
  - Unit tests for endpoint (rate limit, attestation validation, budget cap, heuristic-only-anon path)
- **Afternoon (2h)**:
  - Create `Miles0sage/darwin-commons` repo with README, LICENSE (CC-BY-SA default), SCHEMA.md, CONTRIBUTING.md, COC, empty `fingerprints.jsonl` and `quarantine.jsonl`, CI skeleton
  - Generate GPG key for `darwin-commons-bot`, commit public key to repo
  - Manually seed 20 entries by running existing `fixes/` blackboard through staging path
- **Evening (2h)**:
  - Implement `commons-sync.py` (idempotent, restart-safe, reads staging → verifies → commits)
  - Implement `commons-verify.yml` CI workflow
  - Systemd timer entry (15-min interval) OR cron entry (for dev)
- **Commit checkpoint**:
  - `darwin-mvp`: "feat: public heal endpoint + staging + cron sync"
  - `darwin-commons`: "init: schema + 20 seed fingerprints"

### Day 2 (Apr 25)
- **Morning (2h)**:
  - Implement DLQ/quarantine schema + `commons_triage.py` CLI tool
  - Test: deliberately inject a broken transformer, verify it lands in quarantine with proper metadata
- **Afternoon (2h)**:
  - Implement `X-Darwin-Commons-Credit` header + `/darwin/commons/badge/<contributor_hash>` SVG endpoint
  - Shields.io-compatible output (`{"schemaVersion":1,"label":"darwin commons","message":"42 fingerprints","color":"blue"}`)
  - Badge instructions in README: "`![Darwin Commons](https://your-server/darwin/commons/badge/ch-XXX)`"
- **Evening (2h)**:
  - Write launch content: Show HN post, X thread, LinkedIn post, r/LocalLLaMA post, asciinema script
  - All content centers the Commons counter + contributor-credit loop (NOT mechanism talk)
- **Commit checkpoint**:
  - `darwin-mvp`: "feat: DLQ + contributor badge + triage CLI"

### Day 3 (Apr 26)
- **Morning (3h)**:
  - Record asciinema: fire 10 distinct bugs at public endpoint, Commons counter grows live, hit cache on 11th similar bug → 0 LLM cost demonstrated
  - Upload to asciinema.org, embed in both repo READMEs
- **Afternoon (2h)**:
  - Fire launch wave (staggered):
    - 09:00 PT: Show HN (title focuses on Commons, not mechanism)
    - 09:30 PT: X thread
    - 10:00 PT: LinkedIn
    - 10:30 PT: r/LocalLLaMA
    - 11:00 PT: DM Agent Lightning maintainers + pfix maintainer + Aider maintainer (no PR, just "built this, interested?")
- **Evening (2h)**:
  - Monitor first 6 hours of HN (most of the traction happens here)
  - Respond to issues/PRs in real-time
  - Fix anything broken without re-architecting

## Risks + mitigations (panel-hardened)

| Risk | Mitigation |
|---|---|
| Git-push-on-heal race condition | **ELIMINATED**: cron-batched from staging, 15-min intervals, idempotent sync |
| DLQ becomes graveyard | `commons_triage.py` CLI + 7-day auto-prune + quarantine log surfaces daily summary |
| License-laundering via public submissions | Explicit attestation phrase required; `attestation_phrase_sha256` stored in provenance; legal fallback: CC-BY-SA-4.0 is itself a well-tested license |
| MSFT PR deferred — no 17K⭐ distribution | Accepted. Month-2 work. 72h focuses on HN + organic. |
| Launch lands at 0 stars again | Commons counter is evergreen content: every 10 new fingerprints = tweetable. Contributor badge viral-loop. |
| Public endpoint abuse (spam, DoS) | Rate limit 10/hr/IP + payload caps + kill switch env var + heuristic-only-anon |
| Gemini budget overrun | $5/day cap + 429 on LLM-path over budget + BYO-key bypass |
| Privacy leak in tracebacks | Path anonymization (`/home/*/` → `/home/.../`), 8KB source cap, opt-in publish default=off |
| Quarantined entries dropped silently | Daily quarantine summary auto-posted to a GitHub Discussions thread OR logged to `darwin-mvp/logs/quarantine.log` with rotation |

## What's explicitly in 72h scope

- `darwin-mvp`: 3 new routes, staging write path, cron-batched sync script, DLQ/quarantine, triage CLI, budget/rate-limit middleware
- `darwin-commons`: new public repo with schema, seed entries, 2 CI workflows, GPG trust chain
- Launch: asciinema + HN + X + LinkedIn + Reddit + DM outreach

## What's explicitly OUT of 72h scope

- `agentlightning-darwin` plugin (month-2)
- `microsoft/agent-lightning` PR (month-2)
- Discord server / GitHub Discussions setup beyond basic (month-2)
- IETF RFC / OpenAPI conformance suite (month-2+ only if Commons takes off)
- Ed25519 signing chain (deferred; GPG-signed git commits used instead)
- Multi-model consensus repair
- Regression-test generator
- Enterprise / paid tiers

## Open Questions (reduced to 2 after panel)

1. **License choice — final call by Day 1 morning.** CC-BY-SA-4.0 (default; strongest attribution, weakest adoption in restrictive corp envs) OR Apache-2.0 (broader adoption, weaker attribution but still required). Gemini OK with either + attestation gate; GLM prefers permissive. **Decision window: 30 minutes on Day 1 morning, user makes the call.**
2. **Contributor badge: where does it live?** Hosted on `darwin-mvp` server (simpler, but ties badge availability to server uptime) OR pushed as a static SVG to `darwin-commons/badges/<hash>.svg` on each nightly CI run (more durable, decoupled from server). **Default: static SVG in repo, pushed by `commons-credit.yml`.**

## Closed panel questions

- Bot account: `darwin-commons-bot` (approved)
- Trust chain: **GPG-signed commits** (changed from Ed25519 per panel)
- Daily budget: $5/day (approved)
- Discord/Discussions: Discussions-only, Day 3 setup (downscoped)

## Next step

Self-review pass (placeholders, internal consistency, scope, ambiguity) → commit updated spec → user reviews written v2 → if approved, invoke writing-plans skill to break this into ordered implementation tasks with verification steps.
