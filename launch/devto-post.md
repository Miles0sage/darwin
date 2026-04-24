# Durable Patch Execution for Python Agents — Beyond "Self-Healing"

*Why I stopped saying "self-heal" and what I built instead.*

---

## 1. Why I built this

I kept fixing the same five bugs across four of my own agent repos — a LangChain `AttributeError` here, a Transformers device mismatch there, an OpenAI SDK keyword-arg regression everywhere. The third time I copy-pasted the same patch I stopped and wrote a fingerprint primitive.

Then I wondered: would the same fix recipe apply to bugs other people are filing on GitHub?

Darwin is what happened. It is not a product. It is a **AST-level structural patching layer** — AST-gated, fingerprint-cached, vendor-neutral — and a 261-bug public benchmark built from real HuggingFace, LangChain, PyTorch, and Stack Overflow issues.

NeoCognition's recent analysis estimated production AI agents succeed about 50% of the time. The remediation layer is where the gap closes. It is also where $114.8M of fresh venture money landed in the last 30 days. So the question is not *whether* agent reliability matters but *what specifically to build*.

## 2. Why "self-heal" is a tainted phrase

Read the YouTube comments on any talk titled "self-healing agents" and you will find the same critique: *"that's just try/except."* They are not wrong. Most self-heal demos are:

1. Catch the exception.
2. Ask an LLM for a fix.
3. Apply the fix blindly.
4. Rerun.

Every step has a failure mode. The third step is where hallucinations get promoted to production.

Darwin is narrower and more honest:

- **Triage on entry** — classify every failure as `FIXABLE`, `FLAKY`, or `HUMAN_NEEDED` before any LLM call. A `TimeoutError` or `503 UNAVAILABLE` is flaky — we do not patch it. A `PermissionError` on `/etc/` needs a human — we do not patch it.
- **Durable patch execution** — when we do patch, it is a LibCST transformer compiled from the LLM output, AST-gated for safety (no new broad `except`, no dropped handlers, must parse), then keyed by a fingerprint of the traceback.
- **Bounded blast radius** — each attempt is scoped by a $50/mo LLM spend circuit breaker, a signed-template whitelist, and a one-env-var kill-switch.

## 3. The fingerprint idea

Traceback strings are noisy. The same `AttributeError: 'NoneType' object has no attribute 'text'` can happen in twenty repos with twenty different variable names and twenty different file paths. Regex-matching on the raw traceback misses all of them.

The fingerprint normalizes identifiers to positional placeholders and hashes the structural shape of the error. Two different Python processes can produce the same fingerprint.

Which means: a patch recipe learned from one repo can apply to another. You heal a bug in repo A, the LibCST transformer is exported, repo B with the same fingerprint autopatches itself with zero LLM call.

Helix does this at the runtime-strategy level (retry/backoff/switch-provider). Darwin does it at the source-code level (AST transformers). That is the structural wedge.

## 4. The crossfeed protocol

If the patch recipe travels, the next question is privacy. Darwin's crossfeed is:

- Each recipe is wrapped in a `CrossfeedMessage` containing the fingerprint, the AST-signature hash, the LibCST transformer source, a Q-value, a Laplace-DP-noised Q-delta (ε=1.0, LR=0.3), a timestamp, and a hashed repo_id.
- HMAC-SHA256 signature over all fields; constant-time verify on receive.
- Transport is signed JSON over HTTP.
- Raw source code NEVER crosses the wire — only the AST transformer (the pattern, not the data).

The Laplacian differential privacy is the part that makes cross-tenant sharing viable. Each agent's success/failure signal is perturbed before it joins the federated Q-value. Nobody learns another tenant's exact failure rate, but everyone converges on which recipes are reliable.

## 5. Honest numbers

Three corpora, all public, all permissively licensed.

| Corpus | N | Heal rate | Notes |
|---|---|---|---|
| v1 runnable | 18 | **94.4%** (17/18) | 14 cache hits + 3 Gemini calls + 1 structurally unhealable (user pasted traceback-as-source) |
| v3 strict | 50 | **100%** (50/50) | 34 Gemini + 16 Opus rescues, $2.40 Opus spend |
| v2big-r2 complex | 171 | **~77%** | Gemini Flash rate-limited (503s) → Pro fallback. $1.09 total LLM spend. |

A controlled 20-agent matrix gave **Opus 12/12 vs Gemini 2/12**, which is the honest multi-provider signal even if the numbers are small.

## 6. What is NOT ready

- **Zero paying users. Zero design partners. One contributor.** This is beta.
- The cross-repo crossfeed has been demonstrated in-process between three toy repos. It has not run between two real machines yet.
- There is no head-to-head benchmark against LangChain Open SWE or Self-Healing-SRE-Agent. I plan to run one.
- There is no arXiv preprint. I plan to write one.
- There is no enterprise SOC2 / DPA / audit trail. That is what a design-partner pilot is for.

## 7. What I want

If you run Python agents in production and spend time fixing the same patterns across repos, try it. The repo is MIT, the benchmark is reproducible, the results JSON has every bug URL, and the safety layer is opt-in-enforced by default.

Design partners especially welcome. I am looking for one EU-based intralogistics or warehouse-ops shop that runs LangChain / OpenAI / bespoke Python agents in production and would trade a 6-week paid pilot for a case study. Specifics: €15K, measurable KPI as success criterion, self-hosted (no data leaves your infrastructure), GDPR-compliant by design.

Repo: **github.com/Miles0sage/darwin**
Benchmark JSONs: `/benchmarks/v1/`, `/v2/`, `/v3/`
Asciinema demo: (link in repo README)
Contact: LinkedIn or repo issues.

MIT. Beta. Honest.
