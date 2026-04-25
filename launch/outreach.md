# Darwin v0 Launch — Outreach Drafts

**Status:** drafts only. NOT sent. Verify every handle before send.
**Date drafted:** 2026-04-25
**Repo:** https://github.com/Miles0sage/darwin
**Benchmark:** https://github.com/Miles0sage/darwin/blob/master/BENCHMARK.md
**Dashboard:** local-only at `darwin-mvp/dashboard/index.html` — Miles to host on GH Pages or vercel before send (TODO)
**Goal:** feedback first, amplification never.

---

## A. Skeptic Audience — three honest critics

Approach: humble, "tear this apart," no ask for retweets, no implied promise of co-marketing.

### A1. Nathan Lambert (ML researcher / agent-benchmark critic)

- **Name:** Nathan Lambert (Allen Institute for AI / Interconnects newsletter)
- **Handle:** `@natolambert` on X (verify: https://x.com/natolambert)
- **Email:** publicly listed via `interconnects.ai` author page — TODO verify before send
- **Why him:** has repeatedly written critique of LLM eval methodology in 2025-2026 (Interconnects: "the benchmarks lie"), runs RewardBench, and engages with people who post small-N benchmark caveats. He will tell us if our 50-bug v3 corpus is bullshit faster than anyone.
- **Subject (≤60):** Asking you to tear apart a 50-bug agent benchmark
- **Body (≤150 words):**

> Nathan — long-time Interconnects reader. Apologies for the cold DM.
>
> I shipped a small thing called Darwin: AST-level structural patches for Python agent failures, keyed on identifier-normalized traceback fingerprints. Repo: github.com/Miles0sage/darwin. Benchmark write-up: BENCHMARK.md.
>
> I'm publishing v0 numbers (100% on 50 strict-repro real bugs, 77% on 171 noisy bugs, controlled Opus 12/12 vs Gemini Flash 2/12). I know the N is tiny, I know the corpus is harvested from public GitHub issues which has selection bias, and I know the "100%" framing can be misread.
>
> Before I post wider, would you spend 10 minutes telling me what you'd punch holes in? I won't ask you to share or amplify — just want the honest critique while I can still fix it. Reply or DM whatever's easier.
>
> — Miles

---

### A2. Charity Majors (SRE / observability practitioner)

- **Name:** Charity Majors (CTO, Honeycomb)
- **Handle:** `@mipsytipsy` on X (verify: https://x.com/mipsytipsy)
- **Email:** publicly: charity@honeycomb.io (verify before send — TODO)
- **Why her:** the loudest SRE voice on "automated remediation is dangerous" and on observability for production systems. She has publicly torn apart "self-heal" pitches from operators-of-Kubernetes for years — exactly the right adversarial reviewer for a system that writes patches into production code on a webhook trigger. If she says "your blast radius story is incoherent," we kill the launch and fix it.
- **Subject (≤60):** SRE sanity check on a webhook that patches code
- **Body (≤150 words):**

> Charity — apologies for the cold note. Built something that needs an SRE sanity check before I publish wider.
>
> Darwin: production telemetry → traceback fingerprint → cached LibCST transformer → AST-gated patch written back to the failing repo. Webhook-driven. Bounded blast radius via signed-template whitelist + circuit breaker + kill switch.
>
> Repo: github.com/Miles0sage/darwin. The README explicitly is NOT pitching "self-heal" — I know that phrase is tainted. But I want someone who has shipped real on-call rotations to read the blast-radius design and tell me where it's still wrong.
>
> Specifically: would the AST gate + signed whitelist actually prevent a bad patch from reaching prod, or am I fooling myself? 30 minutes of "this is incoherent because…" would change my next sprint.
>
> Won't ask you to amplify. Just want the punch list.
>
> — Miles

---

### A3. Sarah Guo (early-stage VC, agent infra portfolio)

- **Name:** Sarah Guo (Conviction Capital, ex-Greylock)
- **Handle:** `@saranormous` on X (verify: https://x.com/saranormous)
- **Email:** sarah@conviction.com publicly listed on conviction.com — TODO verify before send
- **Why her:** runs Conviction (agent-infra-heavy fund: Cognition, Harvey, Sierra, etc.), hosts No Priors podcast where she presses founders on moats, and has tweeted explicit criticism of "vibe-only agent demos" in 2025. She will tell us if "Darwin Commons / failure-pair corpus" is a defensible primitive or a feature anyone can copy in a week.
- **Subject (≤60):** Honest critique on an agent-reliability primitive
- **Body (≤150 words):**

> Sarah — long-time No Priors listener.
>
> Building Darwin: an outer-loop agent reliability layer. Production traceback → fingerprint → cached LibCST transformer → cross-repo deterministic patch. Public corpus at github.com/Miles0sage/darwin-commons (CC-BY-SA, GPG-signed fingerprints).
>
> I'm not pitching for funding. Solo, no users yet, EU-based. What I'm asking: would you spend 15 minutes telling me whether the moat thesis ("the corpus compounds, the primitive doesn't") survives a real adversarial read? You've crushed enough founder pitches on No Priors that I'd rather get the honest "this is a feature not a company" before I waste six months.
>
> Repo + BENCHMARK.md + caveats: github.com/Miles0sage/darwin
>
> No ask beyond critique. Won't follow up beyond once.
>
> — Miles

---

## B. Maintainer Audience — three framework pitches

Approach: cite their actual recent commit/PR, pitch the genome primitive, ask for a "bless v0" not a code merge.

### B1. langchain-ai/langgraph

**Top 2 active human committers (last 30 days, since 2026-03-26):**

| Login | Name | Commits | Recent specific work |
|---|---|---|---|
| `hinthornw` | William Hinthorn | 16 | PR #7599 "chore: node-level timeouts" (2026-04-25); long-time core maintainer at LangChain |
| `eyurtsev` | Eugene Yurtsev | 9 | PR #7512 "feat(prebuilt): expose available tools on ToolRuntime" (2026-04-17), release(langgraph) 1.1.7-1.1.8 |

(Note: `dependabot[bot]` had 48 commits but is excluded as a bot. `sydney-runkle` had 8, `ccurme` 3. All real maintainers.)

**Most-promising candidate:** William FH (`hinthornw`, X: `@WHinthorn`)
**Reason:** he owns the PR-review queue on the core stream/runtime path; node-level timeouts (#7599) is exactly the territory where a Darwin-style failure→patch primitive plugs in. He's the gatekeeper of what becomes "official" LangGraph behavior.

**DM draft:**

- **Subject (≤60):** v0 standard for failure-fingerprinting in LangGraph?
- **Body (≤200 words):**

> William — saw your node-level timeouts PR (#7599) land yesterday. The "what does the fleet do when a node hard-errors after timeout" question is exactly why I'm reaching out.
>
> Built Darwin: production traceback → identifier-normalized fingerprint → cached LibCST transformer → AST-gated patch. Vendor-neutral, MIT, Python-only. v0 numbers honest: 100% on 50 strict bugs, 77% on 171 noisy bugs ($1.09 LLM spend), Opus 12/12 vs Gemini Flash 2/12. Repo: github.com/Miles0sage/darwin. BENCHMARK.md cites every JSON.
>
> The pitch: I want the genome primitive — `(stack_fingerprint → LibCST transformer)` pairs, GPG-signed, CC-BY-SA — to become the *de facto* failure-tracking format inside LangGraph's StateGraph error path. Not a hard dependency. Just a documented hook so when a node hits an unhandled exception, it can emit the fingerprint and (optionally) consume cached fixes.
>
> Would you bless this as the v0 standard for failure tracking in LangGraph? Ten-minute review of the data model is enough — I'll do all the integration work.
>
> Dashboard + corpus: github.com/Miles0sage/darwin-commons
>
> No ask beyond yes/no/"go away."
>
> — Miles

**Pre-written reply if "interested but no time":**

> Totally fair. I'll do all the work — the only thing I need from you is a 30-min screen-share where I walk you through the data model + the integration point, and you tell me where it's wrong. Pick any time on Calendly: [TODO insert link]. If after the 30 min you say "no, this doesn't fit LangGraph's contract," I drop it without arguing. Promise.

---

### B2. crewAIInc/crewAI

**Top 2 active human committers (last 30 days, since 2026-03-26):**

| Login | Name | Commits | Recent specific work |
|---|---|---|---|
| `greysonlalonde` | Greyson LaLonde | 80 | dominates the repo. Commit 69d777c "fix(flow): replay recorded method events on checkpoint resume" (2026-04-23), commit 55937d7 "feat: emit lifecycle events for checkpoint operations" (2026-04-23) |
| `lorenzejay` | Lorenze Jay | 7 | commit 3f76374 "feat: supporting e2b" (2026-04-22), commit c77f163 "fix: preserve metadata-only agent skills" (2026-04-23) |

**Most-promising candidate:** Greyson LaLonde (`greysonlalonde`, email: greyson.r.lalonde@gmail.com — listed publicly on GitHub)
**Reason:** clearly the hands-on lead of CrewAI's core right now. The lifecycle-events for checkpoint ops PR (55937d7) and the replay-events-on-resume fix (69d777c) are *the* failure-recovery surface in CrewAI. He's the right person to bless a fingerprint-emit hook on those events.

**DM draft:**

- **Subject (≤60):** Failure-fingerprint hook for crew lifecycle events?
- **Body (≤200 words):**

> Greyson — your two flow checkpoint commits this week (`55937d7` lifecycle events + `69d777c` replay-events-on-resume) are exactly the surface I want to ask about.
>
> Built Darwin: when a Python agent crashes, identifier-normalize the traceback, hash to a 16-char fingerprint, look up a cached LibCST transformer, AST-gate it, write the patch back. Vendor-neutral, MIT. 100% on 50 strict-repro bugs, 77% on 171 noisy bugs. Repo: github.com/Miles0sage/darwin. BENCHMARK.md.
>
> Pitch: emit the fingerprint as one more lifecycle event when a `Crew` / `Flow` checkpoint replays into an unhandled exception. That's it. No code dependency, just a documented event payload shape. Then crews can opt in to the public corpus at github.com/Miles0sage/darwin-commons (CC-BY-SA, GPG-signed) and benefit from cache hits across the whole user base.
>
> Would you bless `(stack_fingerprint, transformer)` as the v0 failure-tracking format inside CrewAI's lifecycle event bus? Ten-minute review of the event-payload shape is all I need; I'll send the PR.
>
> — Miles

**Pre-written reply if "interested but no time":**

> Fair. Then I'll write the PR + the docs + the test, and the only ask becomes a 20-min review on the wire format. If on the call you say "this doesn't belong in CrewAI's contract," I close the PR — no debate. Calendly: [TODO insert link].

---

### B3. langchain-ai/langchain

**Top 2 active human committers (last 30 days, since 2026-03-26):**

| Login | Name | Commits | Recent specific work |
|---|---|---|---|
| `mdrxy` | Mason Daugherty | 21 | PR #36994 "fix(openai): add gpt-5.5 pro to Responses API check" (2026-04-24), PR #36975 "fix(fireworks): swap undeployed Kimi K2 slug" (2026-04-23) — partner-package release captain |
| `ccurme` | (ccurme — name not public) | 21 | release(core) 1.3.1 (2026-04-23), release(openai) 1.1.16 (2026-04-21) — release captain |

(Note: `dependabot[bot]` 35 + `langchain-model-profile-bot[bot]` 7 excluded as bots. `nick-hollon-lc` 5, `jacoblee93` 3, `eyurtsev` 3.)

**Most-promising candidate:** Mason Daugherty (`mdrxy`, X: `@masondrxy`, blog: mdrxy.com)
**Reason:** owns the partner-package release path right now. The OpenAI streaming-hangs fix (PR #36949 by `phvash`, but mdrxy reviewed/released) and the model-profile bot infrastructure show he's the human who decides what counts as a stable contract for LangChain integrations. Better fit than `ccurme` because mdrxy engages with external contributors more visibly.

**DM draft:**

- **Subject (≤60):** Failure-fingerprint format for langchain partner pkgs?
- **Body (≤200 words):**

> Mason — saw the gpt-5.5-pro Responses API fix (#36994) and the openai 1.2.1 release this week. Also followed the streaming-hang prevention work (#36949). The "silent streaming hang → user-facing TimeoutError" pattern is exactly the failure shape Darwin is built around.
>
> Built Darwin: traceback → identifier-normalized fingerprint → cached LibCST transformer → AST-gated patch back into the failing repo. Vendor-neutral, MIT. 100% on 50 strict-repro bugs, 77% on 171 noisy. Repo: github.com/Miles0sage/darwin. BENCHMARK.md cites every JSON.
>
> Pitch: I want the genome primitive — `(stack_fingerprint, LibCST transformer)` pairs, GPG-signed, CC-BY-SA — to be the documented failure-tracking format that LangChain partner packages can emit when their integration tests catch a known-failure-pattern. No runtime dependency. Just a JSONL the test suite drops on regression.
>
> Would you bless this as the v0 format? 15-min review of the schema is enough — I'll write the partner-package adapter myself.
>
> Corpus: github.com/Miles0sage/darwin-commons
>
> — Miles

**Pre-written reply if "interested but no time":**

> Got it. Then the only thing I need is a 30-min Zoom where I walk you through the JSONL schema + the test-suite adapter + show one partner package end-to-end. If on the call you say "no, this doesn't fit LangChain's contract," I drop it the same day. Calendly: [TODO insert link].

---

## C. Two-Tweet Pitches

NotebookLM 949a374b not located on disk (search: 0 hits). Per brief, regenerated using same audience criteria.

### C1. NRW Mittelstand CTO version

> **Tweet 1 (276 chars)**
> Mittelstand CTOs: your 47 Python automations crash on the same 6 errors weekly. Each one = a Slack ticket + a junior dev rerun.
>
> Darwin captures the traceback once, extracts a LibCST patch, deterministically applies it everywhere — even repos with different variable names. Zero LLM cost on hit.
>
> **Tweet 2 (266 chars)**
> Honest v0: 100% on 50 strict-repro real bugs. $1.09 LLM spend on 171-bug run. MIT, vendor-neutral, GDPR-friendly (no source code leaves your VPC), DE-resident builder.
>
> Looking for one EU manufacturing/logistics design partner. github.com/Miles0sage/darwin

### C2. LangGraph / CrewAI maintainer version

> **Tweet 1 (272 chars)**
> Hey LangGraph + CrewAI maintainers: every unhandled exception in your users' graphs is a fingerprint waiting to be cached.
>
> Darwin = `(traceback fingerprint → LibCST transformer)` pairs, GPG-signed, CC-BY-SA. Stack-frame-keyed, identifier-normalized so the same fix applies across repos.
>
> **Tweet 2 (276 chars)**
> Asking for v0 blessing, not a hard dep: emit the fingerprint as a lifecycle event when a node crashes. Users opt in to the public corpus → next graph hitting the same shape gets a cache hit, zero LLM cost.
>
> 100/50 strict bugs healed. github.com/Miles0sage/darwin

---

## TODOs for Miles before any send

1. Verify `@natolambert`, `@mipsytipsy`, `@saranormous` accounts are actually the right people (matched names, but check pinned tweet + bio).
2. Confirm public emails for Charity Majors and Sarah Guo — do not guess corporate aliases.
3. Insert real Calendly URL in the three "interested but no time" replies.
4. Host `darwin-mvp/dashboard/index.html` somewhere reachable (GH Pages / Vercel) before sending — currently file:// only.
5. NotebookLM 949a374b: confirm whether the original two-tweet pitches exist; section C is regenerated content, not the original notebook output.
6. Check William Hinthorn's recent X presence — if he's been on hiatus, fall back to `@veryboldbagel` (Eugene Yurtsev) for the LangGraph DM.
7. Greyson LaLonde's email is on his public GitHub profile — that's the address used here, but a 1-line "is this still the best place to reach you" check is wise.
