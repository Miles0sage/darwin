---
title: "The Naive Multi-LLM Benchmark Is Wrong: 5 Findings From Darwin v0"
subtitle: "Same bug, same prompt, opposite outcomes. What 5 LLMs taught us about agent-failure healing."
tags: [llm, benchmarking, langgraph, agents, evals, darwin]
---

# The Naive Multi-LLM Benchmark Is Wrong: 5 Findings From Darwin v0

Same bug. Same prompt. One LLM heals 100%. Another heals 0%.
Not because of capability. Because of format compliance.
Here's what we learned running 5 LLMs against real LangGraph bugs.

---

Darwin is a vendor-neutral agent-failure healing benchmark. We feed each provider a real, closed GitHub issue (with stack trace and offending code) and ask it to produce a structured diagnosis and a patch. The benchmark grades whether the patched code is extractable and parseable.

This post is about v0: 42 LangGraph bugs, 5 providers, one prompt template. The full corpus is 152 bugs across LangChain, LangGraph, llama_index, AutoGen, and CrewAI — we narrowed v0 to LangGraph because the issue threads are cleaner and the repros are reproducible.

The five providers we tested:

- **Opus 4.7** via `claude-cli` (Anthropic's flagship, run through the Claude Code subscription path)
- **Gemini 2.5 Flash** (Google)
- **Alibaba `qwen-coder-plus`**
- **GLM-4.6** (Zhipu, Z.ai coding plan endpoint)
- **Heuristic-only** (control: regex patches, no LLM)

Same prompt template across all of them. It asks for a `DIAGNOSIS:` block in plain English, followed by a `FIXED_CODE:` block containing a fenced Python code block.

Here's what shook out.

## 1. Provider variance is real and large

Early sample (first 5 bugs of the 42-bug LangGraph corpus, scored on heal/no-heal):

| Provider | Heal rate | Avg latency | Avg patch length |
|---|---|---|---|
| Gemini 2.5 Flash | 38/42 (90.5%) | 26.3s | 2,006 chars |
| Alibaba qwen-coder-plus | 38/42 (90.5%) | 7.2s | 1,638 chars |
| GLM-4.6 | 30/42 (71.4%) | 156.3s | 881 chars |
| Opus 4.7 (claude-cli) | 4/42 (9.5%) | 63.2s | 45 chars (extractor rejection) |
| Heuristic-only | 0/42 (0%) | 0s | 0 chars |

210 graded heals. 39 of 42 bugs show provider disagreement. Only 1 bug had identical heal-status across all 5 providers. The variance, not the mean, is the substance.

The headline isn't "Gemini wins." The headline is that two providers receiving the exact same bytes — same system prompt, same user prompt, same expected response shape — produced completely opposite outcomes. That gap is not noise. It is structural.

This is the part of every multi-LLM leaderboard nobody likes to talk about: the prompt is doing as much work as the model.

## 2. The Opus surprise: format mismatch, not capability

When we cracked open Opus's 0/5 result, every single response had a correct diagnosis. In plain English. With no fenced code block.

A typical Opus response looked like this:

```
The issue is that LangGraph's executor binds tool calls to a stale graph
version when the workflow is recompiled mid-run. The recompile mutates
the channel registry but the executor caches the previous channel ids,
so subsequent dispatches route to dead handlers. You can either pin the
graph version on dispatch or invalidate the executor on recompile.
```

A typical Gemini response on the same bug looked like this:

```
DIAGNOSIS: Channel registry is mutated on recompile but the executor
holds stale channel ids.

FIXED_CODE:
```python
class Executor:
    def dispatch(self, msg):
        if self._graph_version != self._cached_version:
            self._refresh_channels()
        ...
```
```

Both are right about the bug. Only one is parseable by a downstream extractor.

The reason isn't that Opus can't write Python. It's that we ran Opus through `claude-cli`, which wraps the model inside the Claude Code agentic system prompt. That system prompt nudges the model toward conversational, "here's what's happening" output — and away from rigid format adherence. The harness, not the model, was producing the format mismatch.

The implication is uncomfortable for a lot of public benchmarks: most "we ran one prompt across N providers" comparisons are not measuring the model. They are measuring the model multiplied by the harness multiplied by the system-prompt drift. Without per-provider prompt tuning (or per-harness adapters), the leaderboard reflects compliance, not capability.

We are leaving Opus's 0/5 in the v0 numbers exactly because it makes that point.

## 3. Alibaba qwen-coder-plus: the underrated middleweight

The other story in the table is `qwen-coder-plus`. Same heal rate as Gemini. Roughly 3x faster (~9s vs ~24s). Roughly $0.001 per call.

A few reasons it punches up:

- It supports a `response_format` parameter that pins JSON / structured output server-side.
- It's tuned aggressively for code completion and patch-style outputs.
- The endpoint is fast — single-digit-second p50 even on long prompts.

For an agent-healing loop where a fleet might dispatch hundreds of repair calls per hour, the cost-and-latency profile matters more than a few percentage points of headline accuracy. The "best" LLM for a healing pipeline is rarely the most expensive one. Sometimes it's the boring one with predictable formatting and a sub-10s SLA.

This is a recurring pattern in v0 we expect to see again at the full-42 numbers: format compliance + speed + price beats raw model size for narrow, repeatable tasks.

## 4. Same bug, semantically different patches

Heal rate is binary. Patches are not.

Concrete example from the LangGraph corpus, `langgraph#7420` (executor version skew on recompile):

- **Opus** (when prompted with a stricter format) proposed a monkeypatch on the executor's dispatch path plus a version pin in `requirements.txt`.
- **Gemini** proposed: "this is an environment issue, no code change required — pin the LangGraph version in deploy."

Both are defensible reads of the issue thread. Both could be marked "fixed" in the original repo. Neither is obviously wrong.

This is where the simple `heal/no-heal` rubric breaks. We can verify that a string is parseable Python. We cannot, with the v0 harness, verify that the patch is *correct*, *minimal*, or *non-regressive*. We cannot tell that the no-code-change answer is the better one if the bug is actually a packaging issue.

Patch quality is a much harder problem than heal verification. Open research question for v1: how do we score patch *quality* — minimality, non-regression, semantic faithfulness to the original maintainer's fix — and not just heal/no-heal? Likely directions: AST diff against the maintainer's merged PR, run the original repro against the patched code, second-pass review by an independent model.

We are deliberately publishing v0 with the coarse rubric. Calling out the limit is the point.

## 5. What this benchmark cannot do (yet)

Honest list of v0 limits:

- **Single framework.** v0 is LangGraph only. The full corpus has LangChain, llama_index, AutoGen, and CrewAI bugs. v1 expands to all five.
- **Small N.** 42 bugs is enough for directional results, not for confident ranking. v1 = full 152.
- **Coarse heal verification.** "Extracted code is parseable Python" is a weak gate. It catches format failures (Opus's case) but not subtle semantic bugs in the patch. v1 = run each patch against the original repro, gate on test outcome.
- **No extinction tracking.** Darwin's eventual moat is the failure-pattern dataset that compounds: bugs killed in one framework should propagate fixes to similar bugs elsewhere. With a single corpus, there is no propagation signal yet. That arrives when v1 cross-cuts frameworks.
- **One prompt template.** As section 2 makes painfully clear, "one prompt for all providers" is the methodology that produced the 0% Opus result. v1 will likely add per-provider format adapters and report both naive and tuned numbers side-by-side.

If you want a benchmark that lets one provider win on a press release, this isn't it. If you want a benchmark where the methodology limits are loud and the data is open, read on.

---

## Repo, data, design partners

- **Repo:** github.com/Miles0sage/darwin
- **Dashboard:** `<DASHBOARD_URL>`
- **Methodology + raw `matrix.jsonl`:** in the repo, under `/launch/` and `/benchmarks/`

We are looking for two design partners for v1:

1. **A framework maintainer** (LangGraph, AutoGen, CrewAI, or similar) — someone close enough to the bug stream to tell us when our heal is right and when it's only superficially right.
2. **A production agent operator** running fleets of LLM-driven workflows in prod — someone with real failure logs we can ingest and grade against.

If that's you, the benchmark gets honest grading and you get early access to the failure-pattern dataset. Email: `<YOUR_EMAIL>`.

The next post will be the full-42 numbers with per-provider tuned prompts, plus the first cut at patch-quality scoring. The interesting question isn't *which LLM wins*. It's *which agent harness wins*, once you stop pretending the prompt is a constant.
