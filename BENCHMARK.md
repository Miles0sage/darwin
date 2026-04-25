# Darwin Multi-LLM Variance Benchmark v0

**Branch:** `genome-v0` &nbsp;|&nbsp; **Status:** preliminary &nbsp;|&nbsp; **Slice:** LangGraph (42 bugs)

## Summary

The Darwin Multi-LLM Variance Benchmark is a vendor-neutral healing benchmark
that asks five providers — `claude_cli` (Claude Opus 4.7 via Anthropic Max),
`gemini` (Gemini 2.5 Flash), `alibaba` (`qwen3-coder-plus` on dashscope-intl),
`glm-4.6` (Z.ai coding plan), and a no-LLM `heuristic` control — to propose a
fix for each row in a corpus of real-world Python agent-framework bugs scraped
from public GitHub issues. We record per-provider heal rate, agreement, unique
heals, and per-error-class breakdowns. v0 evaluates the LangGraph slice (42
bugs); v1 will extend to LangChain, LlamaIndex, AutoGen, and CrewAI.

## Why this benchmark exists

As of April 2026 there is no public, vendor-neutral benchmark that asks: *"if
five different LLMs see the same agent-framework crash, do they agree on the
fix?"* SWE-Bench focuses on application repos with hand-curated test gates;
HumanEval and MBPP are synthetic; LiveCodeBench tests competitive programming.
None of these surface **the variance signal** — i.e. which provider catches
which class of failure, where they disagree, and where one provider uniquely
heals a bug that the others miss. That variance signal is the substrate Darwin
uses to compose multi-provider repair pipelines and to grow a vendor-agnostic
failure → fix dataset.

The corpus is the second contribution: 152 real failures from five major
agent-framework repos, with stack trace + reproducer + linked fix-PR where
available. We are not aware of an equivalent public dataset.

## Corpus

| Repo | Rows | File |
|---|---:|---|
| langchain-ai/langgraph | 42 | `datasets/github-failures/langchain-ai-langgraph.jsonl` |
| langchain-ai/langchain | 53 | `datasets/github-failures/langchain-ai-langchain.jsonl` |
| run-llama/llama_index | 46 | `datasets/github-failures/run-llama-llama_index.jsonl` |
| microsoft/autogen | 7 | `datasets/github-failures/microsoft-autogen.jsonl` |
| crewAIInc/crewAI | 4 | `datasets/github-failures/crewAIInc-crewAI.jsonl` |
| **Total** | **152** | `datasets/github-failures/_corpus.jsonl` |

Each row was scraped from a public GitHub issue (`gh issue list … --json`) and
reduced to fields needed for healing: stack trace, error excerpt, repro code
(if posted in body), and the fix PR URL (when present in the issue thread).
Issue bodies are public; we redistribute under MIT and cite each row's
`issue_url` for attribution.

**v0 evaluates the LangGraph slice only.** LangGraph was chosen first because
(a) its issues consistently include reproducers, (b) the framework surface is
small enough that all five providers have plausible coverage, and (c) it
doesn't depend on enterprise SaaS auth the way some Autogen/CrewAI failures
do.

## Schemas

### `matrix.jsonl` — one row per (bug_id, provider)

Produced by `datasets/matrix/run_matrix.py` and `datasets/matrix/glm_pass.py`.

```json
{
  "bug_id":        "langchain-ai/langgraph#7420",
  "provider":      "claude_cli",
  "healed":        false,
  "patch_diff":    "",
  "patch_len":     0,
  "latency_ms":    56991,
  "error_class":   "RuntimeError",
  "error_in_heal": null
}
```

| field | type | notes |
|---|---|---|
| `bug_id` | str | `<repo>#<issue_number>`, matches corpus `id` |
| `provider` | str | one of `claude_cli`, `gemini`, `alibaba`, `glm-4.6`, `heuristic` |
| `healed` | bool | see "Heal definition" below — coarse, do not over-trust |
| `patch_diff` | str | provider's proposed fix (truncated to 8000 chars) |
| `patch_len` | int | length of `patch_diff` in chars |
| `latency_ms` | int | wall-clock time for the call |
| `error_class` | str | crude `KeyError`/`TypeError`/… tag from stderr |
| `error_in_heal` | str \| null | exception/HTTP error string if the call itself failed |

### `summary.json` — aggregate emitted by `aggregate.py`

Computed shape (read from `datasets/matrix/aggregate.py`):

```jsonc
{
  "total_bugs":  42,
  "total_rows": 210,
  "heal_rate_per_provider": {
    "<provider>": {
      "attempted":       <int>,
      "healed":          <int>,
      "rate":            <float 0..1>,
      "errors":          <int>,           // call-level errors (HTTP, exception)
      "avg_latency_ms":  <int>
    }
  },
  "agreement_distribution": {
    "0_providers_healed": <int>,           // bug count
    "1_providers_healed": <int>,
    "2_providers_healed": <int>,
    "3_providers_healed": <int>,
    "4_providers_healed": <int>,
    "5_providers_healed": <int>
  },
  "n_unique_heals":   <int>,               // bugs where exactly 1 provider succeeded
  "n_disagreements":  <int>,               // >=2 healed but patch signatures differ
  "clusters": {
    "<normalized error excerpt key>": {
      "n_bugs": <int>,
      "per_provider": {
        "<provider>": {"attempted": <int>, "healed": <int>, "rate": <float>}
      }
    }
  }
}
```

Side outputs: `unique_heals.jsonl` (one per bug where exactly one provider
healed; lists winner + all results), `disagreements.jsonl` (one per bug where
>=2 providers healed but the patch signatures differ — a length-bucket +
first/last line tuple).

> NOTE: `aggregate.py` does not currently emit a true pairwise agreement
> matrix (e.g. claude_cli vs gemini cell). It emits an `agreement_distribution`
> over *count* of agreeing providers per bug. A pairwise matrix is a TODO.

## Providers

| key | model | endpoint | notes |
|---|---|---|---|
| `claude_cli` | Claude Opus 4.7 | local `claude` CLI (Anthropic Max subscription) | invoked through `darwin_harness.diagnose_via_claude_cli`; ~2s pacing between calls |
| `gemini` | Gemini 2.5 Flash | `generativelanguage.googleapis.com` | uses `GEMINI_API_KEY`; rate limits affect availability |
| `alibaba` | `qwen3-coder-plus` | `https://coding-intl.dashscope.aliyuncs.com/v1` (OpenAI-compat) | uses `ALIBABA_CODING_API_KEY` (== `DASHSCOPE_API_KEY`) |
| `glm-4.6` | `glm-4.6` | `https://api.z.ai/api/coding/paas/v4` | Z.ai coding-plan endpoint, not the bigmodel.cn pay-per-token endpoint |
| `heuristic` | none | local | no-LLM control — currently always returns "no fix" for the LangGraph slice (every row in `matrix.jsonl` shows `patch_len=0`) |

All providers receive the same `DIAGNOSE_PROMPT` from `darwin_harness.py`.
There is **no per-provider prompt tuning** in v0. The output is parsed with
the harness's `_extract_fix` regex (find first ```` ```python ```` fence).

## Heal definition

We call a row `healed=true` when **all** of the following hold:

1. Provider returned a non-empty string.
2. The harness's extractor regex finds a fenced ```` ```python ```` block.
3. `ast.parse(<extracted block>)` succeeds (the `glm_pass.py` definition).
4. The extracted block is longer than 20 characters.

This is **deliberately coarse**. A row marked `healed=true` only means the
provider produced syntactically-valid Python that resembles a patch. It does
**not** mean the patch fixes the bug. See limitations.

> NOTE: The `claude_cli` and `gemini`/`alibaba` paths in `run_matrix.py` use a
> slightly different healing predicate (`is_healed`: non-empty + differs from
> input + len > 5) than the `glm_pass.py` AST-parse predicate. We document
> this drift here and treat it as a known issue (TODO: unify the predicate
> in a single helper before v1).

## Honest limitations

1. **Single framework, 42 bugs.** v0 results generalize only to LangGraph-shaped
   failures. The other 110 corpus rows (LangChain, LlamaIndex, AutoGen, CrewAI)
   are not yet evaluated. Do not extrapolate cross-framework heal rates from
   v0 numbers.
2. **Heal != correct fix.** "Heal" is "produced syntactically-valid Python".
   We have **not** re-run the original reproducer against the patched code,
   nor have we compared the patch to the linked fix-PR. A provider that
   confidently emits a plausible-looking but wrong patch is currently
   indistinguishable from one that emits a correct patch. Verifier-against-
   reproducer is on the v1 roadmap.
3. **Naive prompt only.** All providers receive the same `DIAGNOSE_PROMPT`
   with no provider-specific tuning, no chain-of-thought scaffolding, and no
   retrieval. A 2026-state-of-the-art prompt for any one of these providers
   would likely change the heal rate by tens of points. Treat the numbers as
   a *floor*, not a SOTA claim.
4. **Provider availability shifts heal rates over time.** Gemini in particular
   rate-limits aggressively; a re-run on a different day, with a different
   `RPM` budget, can yield a materially different heal count. The runner
   marks a provider `DOWN` after 3 consecutive failures, which can also
   bias against providers having a bad hour. Re-run multiple times and
   report the distribution, not a single point.
5. **Predicate drift between `run_matrix.py` and `glm_pass.py`.** The four
   original providers use a "non-empty and differs from source" predicate;
   the GLM pass uses an `ast.parse` predicate. Heal counts across providers
   are therefore not strictly comparable until the predicate is unified
   (see TODO above).
6. **Issue-body reproducers are not always self-contained.** Some corpus rows
   list the framework version mismatch as the root cause and the user-visible
   `agent.py` is unchanged. A "correct" provider answer in those cases is
   "this isn't an `agent.py` bug" — but our extractor still demands a Python
   block, so a correct narrative answer can score `healed=false`. This is
   a known false-negative source.

## Reproducibility

### Required environment

| var | get from | used by |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | `gemini` provider |
| `ALIBABA_CODING_API_KEY` | https://dashscope-intl.console.aliyun.com (or set `DASHSCOPE_API_KEY`) | `alibaba` provider |
| `ZHIPU_API_KEY` | https://z.ai (coding plan) — alias `GLM_API_KEY` | `glm-4.6` provider |
| `claude` CLI on PATH | https://docs.anthropic.com/claude/docs/claude-code | `claude_cli` provider (Anthropic Max sub) |

### Dependencies

Minimal install, used by the harness + matrix runner:

```
flask-limiter>=3.5.0
gitpython>=3.1.40
cryptography>=41
```

(`flask-limiter` and `gitpython` are pinned in `requirements.txt`;
`cryptography` is pulled in transitively by `genome.py` for ed25519.)

### Run

```bash
cd /root/claude-code-agentic/darwin-mvp
git checkout genome-v0
./scripts/reproduce.sh
```

`reproduce.sh` performs: (1) env-var check, (2) `pip install -r requirements.txt`,
(3) the four-provider matrix run on the LangGraph corpus, (4) the GLM pass,
(5) `aggregate.py` summary. Expected wall-clock: 30-60 minutes for the
LangGraph slice depending on provider latency / Gemini rate limits.

### Expected outputs

```
datasets/matrix/matrix.jsonl       # 42 bugs * 5 providers = 210 rows
datasets/matrix/summary.json       # aggregate metrics
datasets/matrix/unique_heals.jsonl # bugs healed by exactly 1 provider
datasets/matrix/disagreements.jsonl# bugs >=2 healed with divergent patches
```

The headline summary is printed to stdout by `aggregate.py`.

## Early indicators (5-bug pilot)

> WARNING: The full 42-bug LangGraph run is in progress / has just completed
> as of this writeup. Numbers below are from the first 5 bugs as a pilot
> sanity check, not the final benchmark.

In the 5-bug pilot, we observed (a) `gemini` and `alibaba` healing roughly
similar fractions of LangGraph rows under the naive prompt, (b) `heuristic`
healing zero rows on this slice (expected — LangGraph errors are above the
heuristic surface), and (c) at least one bug where exactly one LLM provider
produced a patch and the others did not — i.e. the variance signal we
designed the benchmark to surface does exist on this corpus.

We will publish the full 42-bug numbers and the cross-provider heatmap with
the v0 release tag once the run completes and is independently re-run on
a fresh clone.

## License

MIT. See `LICENSE`. The corpus rows are derived from public GitHub issue
bodies under each upstream repo's license; we redistribute the scraped
fields under MIT with attribution back to each row's `issue_url`. If you
are an upstream maintainer and want a row removed, open an issue.

## Citation

```bibtex
@misc{darwin2026variance,
  title        = {Darwin Multi-LLM Variance Benchmark v0:
                  A Vendor-Neutral Healing Benchmark for Python Agent-Framework Bugs},
  author       = {Sage, Miles},
  year         = {2026},
  howpublished = {\url{https://github.com/<TODO-public-repo-path>/darwin-mvp}},
  note         = {Branch genome-v0; LangGraph slice (42 bugs).
                  Five providers: Claude Opus 4.7, Gemini 2.5 Flash,
                  qwen3-coder-plus, GLM-4.6, heuristic control.}
}
```

> TODO: replace `<TODO-public-repo-path>` once the public repo URL is final.
