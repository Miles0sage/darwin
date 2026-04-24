# X / Twitter thread — Darwin Commons launch (5 tweets)

## Tweet 1 (274 chars)
Just shipped Darwin Commons — the first public corpus of AI agent failure → LibCST transformer pairs.

20 fingerprints seeded. GPG-signed commits. CC-BY-SA-4.0. Any agent crash that matches a fingerprint gets patched in microseconds, zero LLM calls.

Thread ↓

## Tweet 2 (268 chars)
The primitive: your agent throws → traceback gets identifier-normalized → SHA-256 hash → lookup → cached AST transformer → surgical patch → AST-gate verification → write back.

No inference, no drift, deterministic. Same bug in 3 repos = same transformer fires.

## Tweet 3 (262 chars)
Made a beautiful CLI for contributing + browsing.

$ darwin stats → sparkline of corpus growth + top error shapes
$ darwin browse → syntax-highlighted transformers
$ darwin triage → vim-keys to review quarantined patches
$ darwin badge → screenshot-ready contributor card

## Tweet 4 (278 chars)
Contribute: POST your production failure + transformer to /darwin/heal/public with publish_to_commons=true. Rate-limited, attestation-required, sync worker batches every 15min.

The CLI handles everything: darwin submit entry.json

## Tweet 5 (270 chars)
Why open the corpus: Darwin's moat is the primitive, not the cache. Every contributor makes the cache hit rate higher for everyone.

MIT core. CC-BY-SA-4.0 data. Zero vendor lock.

Repo: github.com/Miles0sage/darwin-commons
Demo: [asciinema link once uploaded]
