# Darwin v0 — 6-Tweet Launch Thread

Char counts annotated as `[N/280]`. Placeholders use `<TOKEN>` syntax for grep+replace.

---

## 1/6 — Lede [276/280]

The naive multi-LLM benchmark is wrong.

Same bug. Same prompt. One LLM heals 100%. Another heals 0%.

Not because of capability. Because of format compliance.

5 findings from Darwin v0 — a vendor-neutral agent-failure healing benchmark on real LangGraph bugs.

Thread.

---

## 2/6 — Provider variance [273/280]

Finding 1: provider variance is real and large.

Same prompt, 5 LLMs, 42 real LangGraph bugs:

- Gemini 2.5 Flash: 38/42 = 90.5% (~26s)
- Alibaba qwen-coder-plus: 38/42 = 90.5% (~7s)
- GLM-4.6: 30/42 = 71.4% (~156s)
- Opus 4.7 via claude-cli: 4/42 = 9.5% (~63s)
- Heuristic-only: 0/42 = 0%

39 of 42 bugs show provider disagreement.

---

## 3/6 — The Opus surprise [277/280]

Finding 2: Opus's 0/5 is format mismatch, not capability.

Every Opus response diagnosed the bug correctly. In plain English. With no fenced Python block.

claude-cli wraps Opus in Claude Code's agentic system prompt -> conversational mode -> extractor rejects.

The harness, not the model, lost.

---

## 4/6 — Alibaba is underrated [261/280]

Finding 3: qwen-coder-plus is the underrated middleweight.

Same heal rate as Gemini. ~3x faster (~9s). ~$0.001/call. Native response_format support.

For a fleet dispatching hundreds of repair calls/hr, format compliance + sub-10s p50 beats headline accuracy.

The boring model wins.

---

## 5/6 — Patch quality is unsolved [270/280]

Finding 4: same bug, semantically different patches.

langgraph#7420: Opus proposes monkeypatch + version pin. Gemini proposes "no code change, env issue."

Both defensible. Both could be "right."

heal/no-heal is binary. Patch quality isn't. Open research question for v1.

---

## 6/6 — Limits + CTA [272/280]

Finding 5: what v0 can't do yet.

- 1 framework (LangGraph). v1 = all 5
- 42 bugs. v1 = full 152
- Heal = "parseable Python." v1 = run the repro
- 1 prompt template. v1 = per-provider adapters

Repo: github.com/Miles0sage/darwin
Dashboard: <DASHBOARD_URL>
Design partners wanted: <YOUR_EMAIL>
