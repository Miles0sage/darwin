# X / Twitter thread — 5 tweets

## Tweet 1 (260 chars)
Built Darwin: AST-level structural patching for Python agents. Not "self-heal" (that phrase is tainted).

What it actually does: triages every failure (fixable / flaky / human-needed), AST-gates the patch with LibCST, caches the fingerprint, propagates across repos.

Thread ↓

## Tweet 2 (275 chars)
Primitive: a LibCST transformer is compiled from an LLM patch, validated to parse + not drop exception handlers + preserve semantics, keyed by an identifier-normalized fingerprint of the traceback.

Same error signature, different variable names → same fix applies. That's the moat.

## Tweet 3 (280 chars)
Benchmarks, honest:
— 100% on 50 strict-repro real bugs (v3, Gemini+Opus)
— 94% on 18 runnable bugs from HF / LangChain / PyTorch / SO (v1)
— ~77% on 171 complex real bugs (v2, Gemini rate-limited Flash → Pro fallback)
— 27/27 unit tests green
— $1.09 LLM spend on 171-bug run

## Tweet 4 (280 chars)
Controlled provider matrix: Opus 12/12 vs Gemini 2/12.

But it's vendor-neutral — Gemini Flash primary, Gemini Pro middle tier, Opus final fallback, all with 3-retry-with-backoff on 503. Works without any Anthropic key if you want.

## Tweet 5 (280 chars)
Also shipped: HMAC-signed + Laplacian-DP federated crossfeed (ε=1.0) so patches can travel across tenants without leaking source code. Kill-switch. Signed-template whitelist. $50/mo circuit breaker.

MIT, 261-bug public benchmark, solo beta.

github.com/Miles0sage/darwin
