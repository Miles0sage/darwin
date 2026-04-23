# Darwin Commons — 72-Hour Blitz Design
Date: 2026-04-24
Author: Miles (via Claude Opus 4.7)
Status: Proposed — awaiting user approval

## Goal

Convert Darwin from "OSS self-heal tool with 0 stars" to "the reliability data commons for the agent ecosystem" in 72 hours. Start the failure-corpus flywheel publicly, ship a viral Agent Lightning integration surface, and make the protocol spec emergent (whatever the endpoint returns becomes the spec).

## The one-sentence pitch

**"Every time Darwin heals a failing agent, the fingerprint→transformer pair is minted into a public, CC-BY-SA, cryptographically-attributed data commons that every other self-heal tool in the ecosystem can read, contribute to, and benchmark against."**

## Non-goals (72h window)

- No IETF RFC, no OpenAPI spec document, no conformance suite (all month 2+)
- No payment / tiered access / enterprise license (all month 2+)
- No multi-model consensus repair, no regression-net generator (deferred to later brainstorm combos)
- No rewriting `darwin-mvp` internals; we add surfaces on top

## Architecture

Three new surfaces bolted onto the existing `darwin-mvp`:

```
+-------------------------+     +-------------------------+
|  darwin-mvp (existing)  |     |   darwin-commons (new)  |
|  webhook_ingest.py      |---->|   fingerprints.jsonl    |
|  patch.py, triage.py    |     |   transformers/*.py     |
|  (unchanged)            |     |   attribution.json      |
+-------------------------+     +-------------------------+
           |
           |  /darwin/heal/public    (new endpoint, rate-limited)
           |  /darwin/commons/list   (new endpoint, read-only)
           v
+-------------------------+     +-------------------------+
|  public HTTP surface    |<----|  agentlightning-darwin  |
|  (same port 7777)       |     |  plugin (new repo)      |
+-------------------------+     +-------------------------+
```

### Component 1 — Public heal endpoint

Adds two routes to `webhook_ingest.py`:

- `POST /darwin/heal/public` — open, rate-limited (10/hr/IP by default, tunable via env).
  - Body: same JSON schema as existing `/darwin/failure`.
  - Abuse surface: spam tracebacks, malicious source code. Mitigations: IP rate limit, max payload 16KB, source_code length cap 8KB, reject if traceback doesn't parse as a Python traceback, heuristic-only on anonymous (Gemini path requires signed request with user key).
  - Response: same schema as existing, PLUS `commons_entry_id` if the heal minted a new entry.
- `GET /darwin/commons/list?limit=100&since=<timestamp>` — public read of fingerprints (no source code, only fingerprint + error class + transformer hash + provenance).

Budget cap: env var `DARWIN_PUBLIC_DAILY_BUDGET_USD=5` enforced via a simple in-memory counter + file-persisted daily rollover. Over budget → heuristic fallback only + 429 for cache-miss LLM paths.

### Component 2 — Darwin Commons data repo

A **separate public GitHub repo** `Miles0sage/darwin-commons`:

```
darwin-commons/
├── README.md                     ← "The public failure→transformer corpus"
├── LICENSE                       ← CC-BY-SA-4.0
├── CONTRIBUTING.md               ← how to submit a fingerprint manually
├── fingerprints.jsonl            ← append-only JSONL, one entry per cached transformer
├── transformers/
│   └── <fingerprint>.py          ← LibCST CSTTransformer source, one file per entry
├── attribution.json              ← signed attribution chain
├── SCHEMA.md                     ← the JSONL schema (doubles as the emergent "protocol")
└── .github/workflows/
    └── commons-verify.yml        ← CI that replays every entry on fresh checkout, fails if any transformer doesn't parse or fails its self-test
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
    "origin_agent": "anonymous-<hash>",
    "public_heal_id": "ph-2026-04-24-000042",
    "ingestion_ip_hash": "sha256(salt + ip)"
  },
  "license": "CC-BY-SA-4.0",
  "attestation_sig": "<ed25519 sig by Darwin server key>"
}
```

Writes to the commons are gated by the Darwin server's Ed25519 key; the commons repo enforces via CI that every entry has a valid signature. Anyone can fork + self-attest their own contributions.

**Ingestion flow:**
1. Public `/darwin/heal/public` request creates a fix in-memory.
2. Darwin server asynchronously `git push`es the new entry to `darwin-commons` via a dedicated bot account (`darwin-commons-bot`) with narrow repo permissions.
3. README live-counter in `darwin-mvp` queries commons API for total entries, updates via GitHub Actions nightly.

**Abuse: what if someone poisons the commons with a malicious transformer?** Mitigation: every transformer runs under Darwin's existing AST diff gate BEFORE being written, so malicious ones are rejected upstream. Additionally, a CI job on the commons repo runs every transformer against a synthetic traceback + source pair and asserts the output AST is a bounded diff. Transformers that fail CI get quarantined.

### Component 3 — agentlightning-darwin plugin

A **third new repo** `Miles0sage/agentlightning-darwin`:

```python
# agentlightning_darwin/__init__.py
from agentlightning import LitAgent  # or span emitter hook
import httpx

class DarwinFailureEmitter:
    """Drops into Agent Lightning's span pipeline.
    On any rollout that ends in an exception, POST the traceback +
    offending source to a Darwin endpoint (public or private), store
    the patch in the rollout span as a 'darwin.patch' attribute for
    RL algorithms to use as a reward signal.
    """
    def __init__(self, endpoint="http://127.0.0.1:7777/darwin/heal/public",
                 api_key=None):
        self.endpoint = endpoint
        self.api_key = api_key

    def on_rollout_end(self, task, rollout, runner, tracer):
        if rollout.error is None:
            return
        payload = {
            "originating_agent": f"agl-{task.id}",
            "stderr": rollout.error.traceback,
            "source_code": rollout.error.source_code,
        }
        headers = {}
        if self.api_key:
            headers["x-darwin-key"] = self.api_key
        r = httpx.post(self.endpoint, json=payload, headers=headers, timeout=30)
        if r.status_code == 200:
            rollout.metadata["darwin.patch"] = r.json().get("new_source")
            rollout.metadata["darwin.commons_entry_id"] = r.json().get("commons_entry_id")
```

Ship as `pip install agentlightning-darwin`. One-line integration in any Agent Lightning-based RL training loop. Bonus: the RL algorithm now gets a new signal: `did_patch_reduce_future_error_rate`.

**PR to `microsoft/agent-lightning`:** open a PR to `examples/darwin-heal/` showing the plugin in use with one of their existing examples. Even if it sits in review for weeks, the PR itself is marketing surface.

## Data flow

```
Anyone's failing Python agent
  |
  | POST /darwin/heal/public  {traceback, source}
  v
Darwin webhook (existing logic)
  |
  | fingerprint → cache lookup
  |   hit:  return cached transformer + apply   ───────┐
  |   miss: LLM synthesis → AST diff gate → cache       │
  v                                                      │
Response: patched source + commons_entry_id              │
  |                                                      │
  | asynchronously                                       │
  v                                                      │
darwin-commons repo (git push, signed)                   │
  |                                                      │
  | available forever, CC-BY-SA                          │
  v                                                      │
Future requesters hit cache ─────────────────────────────┘
```

## Success metrics (72h)

- **200 fingerprints in Commons** by end of Day 3. (~10 organic heals/day from launch buzz, padded by us deliberately firing real bugs from our 50-bug corpus through the public endpoint for seed coverage.)
- **agentlightning-darwin: 5+ GitHub stars, 1 PR to microsoft/agent-lightning open.**
- **50+ Darwin repo stars** (up from 0). Proxy for traction.
- **1 external contributor** opens an issue or PR on `darwin-commons` or `darwin-mvp`.

Not a metric: revenue. This is moat-building.

## Day-by-day plan

### Day 1 (Apr 24)
- **Morning (4-6h)**: implement `/darwin/heal/public` + rate limit + daily budget cap in `webhook_ingest.py`.
- **Afternoon (2-3h)**: create `Miles0sage/darwin-commons` repo, seed with README, LICENSE, SCHEMA.md, empty `fingerprints.jsonl`, CI skeleton.
- **Evening (2h)**: implement git-push-on-heal bot flow (dedicated bot account + narrow-permission PAT). Seed Commons with 20 entries from our existing `fixes/` blackboard.
- **Commit checkpoint**: Darwin repo `feat: public heal endpoint + commons sync`, Commons repo `init: schema + 20 seed fingerprints`.

### Day 2 (Apr 25)
- **Morning (3h)**: create `Miles0sage/agentlightning-darwin` repo, ship `DarwinFailureEmitter`, write tests with Agent Lightning's mock LitAgent.
- **Afternoon (2h)**: package for PyPI, publish `pip install agentlightning-darwin` (v0.1.0).
- **Evening (2h)**: fork `microsoft/agent-lightning`, add `examples/darwin-heal/`, open PR with a runnable demo that shows a failing rollout getting healed + RL signal added.

### Day 3 (Apr 26)
- **Morning (3h)**: record asciinema showing live public heals populating the Commons counter. Tweet-length copy: "Watch Darwin heal 10 agent repos from one cached fingerprint, contributed to a public data commons anyone can read."
- **Afternoon (2h)**: fire launch wave:
  - HN Show HN post (focus: "The public corpus of agent failures")
  - X thread (focus: "I shipped the first public CC-BY-SA commons of agent failure→transformer pairs")
  - LinkedIn (Mittelstand-flavored: reliability as a commons resource)
  - r/LocalLLaMA post (focus: "vendor-neutral self-heal + dataset you can train on")
  - Direct outreach: DM Agent Lightning maintainers, pfix maintainer, Aider maintainer
- **Evening (2h)**: watch traffic, respond to issues/comments, fix broken things in real time.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Public endpoint abuse (spam tracebacks, CPU DoS) | Rate limit 10/hr/IP, payload size cap, heuristic-only on anonymous requests, kill-switch env var `DARWIN_PUBLIC_DISABLED=1` |
| Budget overrun on Gemini | Hard `$5/day` cap, fall back to heuristic-only when exceeded, daily rollover at UTC midnight |
| Commons gets spam-poisoned transformers | AST diff gate BEFORE write + Commons CI replays each entry + quarantine on failure |
| Agent Lightning PR rejected / sits forever | Plugin works standalone, PR is secondary. Publish as PyPI regardless. |
| Launch lands with 0 engagement again (like repo did) | This time we have content-as-engagement (growing counter), not just announcement. If the Commons counter hits 100 by Day 3, that's a tweet on its own. |
| Privacy: tracebacks may leak user code/paths | `source_code` truncated to 8KB, paths anonymized (`/home/*/` → `/home/.../`), optional opt-in flag to publish unsanitized |
| Someone forks Commons and ignores attribution | CC-BY-SA-4.0 covers us legally; social pressure on attribution; we ship the reference implementation so ours is canonical |

## What we explicitly DON'T do in 72h

- No IETF RFC. The emergent spec is `SCHEMA.md` + the endpoint's JSON response.
- No multi-model consensus (deferred — can add as "premium heal" later)
- No regression-test generator (deferred)
- No enterprise tier (deferred)
- No rewrite of `webhook_ingest.py` internals — additive only
- No `darwin-protocol` repo yet — if Commons takes off, then we formalize. Otherwise the endpoint is the protocol.

## Components to edit in existing code

- `darwin-mvp/webhook_ingest.py` — add 2 new Flask routes (`/heal/public`, `/commons/list`), add rate limiter, add async commons-sync dispatcher
- `darwin-mvp/blackboard.py` — add `publish_to_commons(entry)` method
- `darwin-mvp/README.md` — add Commons counter + link to `darwin-commons` repo in hero
- New: `darwin-mvp/commons_sync.py` — git push worker (uses `GitPython` or subprocess)
- New: `darwin-mvp/requirements.txt` — add `flask-limiter`, `gitpython`, `cryptography` (for Ed25519)

## Components in brand new repos

- `Miles0sage/darwin-commons` — data commons
- `Miles0sage/agentlightning-darwin` — plugin + PyPI package

## Open questions for the user (answer before planning)

1. **Bot GitHub account name** — suggest `darwin-commons-bot`. Acceptable?
2. **Commons signing key** — generate fresh Ed25519 keypair on Day 1, commit public key to Commons repo, keep private key in `darwin-mvp` server env. Acceptable?
3. **Gemini daily budget** — defaulting to $5/day ≈ 500 fresh heals, then heuristic-only. Raise/lower?
4. **Do we want a Discord / GitHub Discussions for Commons contributors?** Cheap to add Day 3 if yes.

## Next step

Self-review pass (placeholders, internal consistency, scope, ambiguity) → commit this spec → user reviews written doc → if approved, invoke writing-plans skill to break this into concrete implementation tasks with ordering and verification steps.
