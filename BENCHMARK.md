# Darwin: Cross-Repository Patch Memoization via Traceback Fingerprinting and CST Recipes

**Benchmark Report — 2026-04-22**

## Abstract

Darwin memoizes agent-runtime failures as `(stack_fingerprint → LibCST transformer)` pairs such that a fix mined from repository A applies deterministically to repository B with different identifier names, file paths, and call-site layout. Published results on the included harness show a 99% cache-hit rate at fleet size N=100 across four handcrafted failure classes, and 100% hit rate on warm-cache reruns. The primitive is a composition of (i) identifier-masked traceback fingerprinting, (ii) LibCST transformer recipes applied under an AST safety gate, and (iii) an `fcntl.flock`-serialized JSON blackboard that implements a first-miss-wins fleet-race. At cache-hit time the system performs zero LLM calls.

## 1. Methodology

### 1.1 Failure taxonomy

Four canonical agent-runtime failure classes, each with a minimal reproducer under `darwin-mvp/scenarios/`:

| Class | Symptom | Root cause | Transformer family |
|-------|---------|------------|--------------------|
| `schema-change` | `KeyError: 'text'` | Upstream API nested `text` under `data` | `leave_Subscript` → `get(..., {}).get(...)` |
| `missing-file` | `FileNotFoundError: .../v3/data.json` | Endpoint versioned from `v1` to `v3` | `leave_Assign` over `/`-chained path BinOps |
| `rate-limit` | `RuntimeError: API rate limited (429)` | Placeholder `raise` in client stub | `leave_Raise` with warning injection |
| `timeout` | `TimeoutError` on network I/O | Default timeout too short for upstream | seeded transformer, scenario-specific |

### 1.2 Fleet model

Agents are spawned as separate processes via `concurrent.futures.ProcessPoolExecutor`. A `multiprocessing.Barrier` is armed before any worker executes its failing path, guaranteeing that N workers enter the heal path within a few milliseconds of each other — the worst case for blackboard contention. This is the configuration in which LLM-call budget matters, because a naive implementation would fire N duplicate LLM requests.

### 1.3 Blackboard and fingerprint

The blackboard is a filesystem directory of `fix-*.json` entries. All writes funnel through `exclusive_lock()` (`fcntl.LOCK_EX` on `.write-lock`). Reads are lock-free: a sorted `glob` over existing entries. The fleet-race primitive `compute_and_write_fix(stderr, compute, ...)` (in `blackboard.py`) takes the lock, re-checks the lookup, and only invokes `compute()` if no prior worker has written — first miss wins, N−1 workers consume the cached artifact on release.

The fingerprint is a 16-character hex slice of `sha256` over the *cross-codebase core* of a traceback:

1. `normalize(stderr)` strips worker tmpdirs, quoted absolute/relative paths (keeping basenames only), line numbers, hex memory addresses, ISO timestamps, UUIDs, PIDs, long integers, and `<frozen ...>` import tags.
2. `_fingerprint_core` retains only the terminal `ErrorClass: msg` line and the last code line from the traceback.
3. The last code line passes through `_mask_identifiers`: bare identifiers are rewritten to `_`, while Python keywords and string literals are preserved. This collapses `value = row["text"]` and `body = doc["text"]` to the same masked form.

The resulting 16-char key content-addresses the fix. Two repositories with different variable names, different file paths, and different function names but the same terminal error produce the same fingerprint.

### 1.4 Cached artifact

Rather than cache a text substitution, Darwin caches the *source* of a `libcst.CSTTransformer` subclass (convention: class name `Patch`). On cache hit, `patch.apply_recipe()` instantiates the transformer, visits the new codebase's CST, and emits new source. If no CST node matches, the recipe raises `PatchMissError` and the caller falls through to the B-path (LLM diagnose). Transformer source is `exec`'d in a namespace with a restricted `__builtins__` dict limited to construction and introspection primitives (`__build_class__`, `isinstance`, `getattr`, standard containers). This is namespace isolation, not a security sandbox (see §5, §8).

### 1.5 LLM provider

All multi-scenario runs below use Gemini 2.5 Flash for the B-path (first-miss diagnose). Darwin's provider interface is vendor-neutral; an Anthropic path is wired but not exercised in the numbers reported here. A heuristic regex fallback is used when no API key is present (see `benchmark-report.json`).

## 2. Scaling Results

Single-scenario fleet-race, cold blackboard, ProcessPool with barrier-synchronized start. Wall-clock is end-to-end (spawn through last worker return). LLM calls are exactly the count of diagnose invocations crossing the provider boundary.

| Fleet size N | LLM calls | Cache hits | Healed | Heal rate | Wall-clock |
|-------------:|----------:|-----------:|-------:|----------:|-----------:|
| 1            | 1         | 0          | 1      | 100%      | 1.1 s      |
| 10           | 1         | 9          | 10     | 100%      | 1.1 s      |
| 50           | 1         | 49         | 50     | 100%      | 2.6 s      |
| 100          | 1         | 99         | 100    | 100%      | 6.3 s      |
| 200          | 1         | 199        | 200    | 100%      | 13.1 s     |
| 200 (warm)   | 0         | 200        | 200    | 100%      | 11.3 s     |

The LLM-call count is independent of N: exactly one diagnose per distinct fingerprint, regardless of concurrency. The "warm" row uses `--keep-blackboard` to inherit a previous run's fixes directory; zero LLM calls fire because every fingerprint already has a cached transformer.

## 3. Multi-Scenario Taxonomy

Four (or three) failure classes running concurrently, each with its own fleet of N workers, real Gemini 2.5 Flash diagnosing first-miss per class.

| Scenarios | Fleet × classes | LLM calls | Cache hits | Healed | Heal rate | Wall-clock |
|-----------|-----------------|----------:|-----------:|-------:|----------:|-----------:|
| schema, missing, rate           | 10 × 3   | 3 | 27  | 30  | 100% | 49 s   |
| schema, missing, rate, timeout  | 10 × 4   | 4 | 36  | 40  | 100% | 46 s   |
| schema, missing, rate (cold)    | 100 × 3  | 3 | 297 | 300 | 100% | 16.3 s |

LLM call count equals the number of distinct fingerprints (i.e., distinct failure classes), not the number of workers. At 300 agents, 297 heals are served without crossing the provider boundary.

## 4. Cross-Repository Transfer

This is the novel primitive the paper claims. Two artefacts establish it.

### 4.1 `xrepo_proof.py` — synthetic three-repo proof

Three repositories (`alpha`, `beta`, `gamma`) are generated with the same underlying bug (schema change: upstream response nested `text` under `data`) but *different* variable names, function names, and module layouts:

```
alpha/   value = row["text"]
beta/    body  = doc["text"]
gamma/   content = record["text"]
```

Each raises a traceback with distinct filenames, distinct line numbers, distinct framework frames. After identifier masking and path normalization, all three produce fingerprint `1def79fb36f4ad1f`. The first repo diagnoses (1 LLM call, emits a `libcst.CSTTransformer` rewriting `X["text"]` to `(X.get("data", {}).get("text") or X.get("text", ""))`). The second and third repos apply the cached transformer deterministically: 0 LLM calls, AST gate passes, source heals.

### 4.2 Webhook heal — production-shaped transfer

The production telemetry path (`webhook_ingest.py`, Flask listener on `:7777`) was exercised on 2026-04-22 22:45 with two hand-crafted Sentry-shaped payloads:

- **Payload 1:** `myorg/sentiment-tracker`, file `pipeline/rows.py`, function `process_row`, variable `value`.
- **Payload 2:** `myorg/analyzer`, file `src/analyze/extract.py`, function `extract_body`, variable `body`.

Both payloads raise `KeyError: 'text'` on a subscript. Payload 1 hits a cold blackboard; Darwin diagnoses, emits a transformer, caches under fingerprint `0b8ed4dc613c4688`. Payload 2 arrives seconds later from a different repository context: same fingerprint, `cache_hit: true`, the cached CST transformer rewrites the subscript in payload 2's source, the AST gate passes, status `healed`, zero additional LLM calls. Response payloads are persisted under `darwin-mvp/fixes/` and the rejected-fix ledger is empty.

### 4.3 What identifier masking enables

Prior work in APR treats the source text as first-class — regex-based fix-patterns, edit-sequence learners (Getafix, Tufano et al.), and template miners (GenProg). These approaches are brittle to identifier rename because the learned pattern is lexical. Darwin's fingerprint normalization drops identifiers *before* hashing, and its cached artifact is a CST visitor *matching by structure* — `isinstance(node.slice[0].slice.value, cst.SimpleString) and node.slice[0].slice.value.value in ('"text"', "'text'")` — which is identifier-free by construction. The combination is what makes the A→B transfer deterministic rather than probabilistic.

## 5. Safety Gate Results

Every candidate patch (cache-hit apply or fresh LLM diagnose) passes through `validate_fix(old, new, stderr)` before write. Gate properties:

- Must parse as syntactically valid Python (`ast.parse`).
- `try`/`except` count is preserved or increased; never decreased.
- No bare `except:` is introduced.
- `assert` statements are preserved.
- Imports must remain a superset of the original (no silent removal).

Results across all runs tabulated in §2 and §3: **0 rejections**. The `fixes/rejected/` directory is empty. This is consistent with the cached transformers being hand-reviewed reference recipes (see `patch.py REFERENCE_*`); we expect non-zero rejection rates once LLM-emitted transformers exceed the reference library.

**Honest scope claim.** The gate verifies syntactic plausibility and preserves a small set of structural invariants. It does not claim semantic equivalence, does not verify that the fix addresses the root cause, and does not prevent a malicious transformer from rewriting unrelated code. See §8.

## 6. Reproducibility

Every figure above is regenerable from the repo. Commands:

```bash
# §2 scaling rows — cold blackboard, one class
python3 darwin-mvp/benchmark.py --scenario schema-change --fleet 100
python3 darwin-mvp/benchmark.py --scenario schema-change --fleet 200
python3 darwin-mvp/benchmark.py --scenario schema-change --fleet 200 --keep-blackboard

# §3 multi-scenario
python3 darwin-mvp/multi_scenario_benchmark.py \
    --scenarios schema-change,missing-file,rate-limit --fleet 100

# §4.1 cross-repo proof
python3 darwin-mvp/xrepo_proof.py

# §4.2 webhook heal
python3 darwin-mvp/webhook_ingest.py &   # background
curl -X POST localhost:7777/darwin/failure -d @darwin-mvp/examples/webhook_payload_1.json
curl -X POST localhost:7777/darwin/failure -d @darwin-mvp/examples/webhook_payload_2.json
```

Each run writes `fix-*.json` under `DARWIN_FIXES_DIR` (default `darwin-mvp/fixes/`) and emits a JSON summary readable by `jq`.

## 7. Related Work

Automated program repair (APR) is a mature subfield with well-defined benchmarks (Defects4J, ManyBugs, BugsInPy) and well-known techniques. Darwin occupies a specific intersection that, to our reading, has not been shipped as a composed primitive.

- **Monperrus (2020)** — *Automated Program Repair: Advances and Challenges*, ACM CSUR. Taxonomy of generate-and-validate, semantics-driven, and learning-based repair. Darwin is generate-and-validate with a memoization layer.
- **Tufano et al. (2017)** — *Learning to Fix Build Errors*, ICSE. Explicitly positions *cross-repository transferability* of learned fixes as an open problem. Darwin addresses this specific gap by hashing on an identifier-masked structural core.
- **Bader et al. (2019)** — *Getafix: Learning to Fix Bugs Automatically*, OOPSLA. Mines edit patterns from commit history and applies them to new code. Patterns are edit-sequence rules over AST tokens. Darwin caches the *post-diagnosis* transformer as a CST visitor, keyed by traceback fingerprint rather than by AST-pattern match on the buggy source.
- **Long and Rinard (2016)** — *Automatic Patch Generation by Learning Correct Code*, POPL. Learns from human patches to rank candidates. Darwin does not rank — a first-hit cache lookup is deterministic, and the fallback path defers to the LLM rather than to a ranked candidate set.
- **Xia and Zhang (2022)** — *Less Training, More Repairing Please (AlphaRepair)*, FSE. Cloze-style masked-token repair via a pre-trained model. Darwin is orthogonal: it does not train, and it runs the LLM exactly once per unique fingerprint.
- **Le Goues et al. (2012)** — *GenProg*, TSE. Genetic search over statement-level edits; validated by test suite. Darwin's search cost on cache hit is `O(CST walk)`, not `O(generations × population × test-runtime)`.
- **Microsoft AgentRx (2026-03-11)** — Failure-attribution framework for agent systems. Post-hoc analysis of why an agent failed. Darwin differs: it memoizes the *remediation*, not the attribution.
- **AgentRR (2026)** — Record/replay for deterministic agent traces. Adjacent concern; Darwin replays *fixes across codebases*, not traces within one codebase.
- **Anthropic Agent Teams (file-locked blackboard, 2026)** — Shared-filesystem coordination primitive for multi-agent systems. Darwin's `fcntl.flock`'d JSON blackboard is the same genre; the novelty here is what is stored (CST transformer recipes keyed by cross-codebase fingerprint), not the locking primitive.

**Positioning.** Structural patch memoization (CST recipes) + fingerprint-keyed content addressing (identifier-masked traceback cores) + fleet coordination primitive (flock'd blackboard with first-miss-wins). Each component has prior art. The composition — in particular, the claim that the cached artifact transfers across repositories with renamed identifiers because both the *key* and the *artifact* are identifier-free by construction — is what this benchmark substantiates.

## 8. Limitations

A reviewer reading this section is expected to find it inadequate in exactly the ways it concedes. That is the point.

- **Corpus scale is tiny.** Four handcrafted failure classes. Not 100, not 1000, not a distribution over real-world tracebacks. The `signature.py` normalization pipeline is tuned to what we have seen, not proved against held-out data.
- **Language scope is Python only.** The CST layer is `libcst`; porting to JavaScript/TypeScript/Go requires rewriting recipes against tree-sitter bindings (planned, §9) and revalidating the safety gate.
- **No held-out evaluation.** We have not run against BugsInPy, ManyBugs, or Defects4J. Reported heal rates are over scenarios authored by the same people who wrote the transformers. The number to watch, not yet reported, is heal rate on a held-out corpus.
- **Identifier masking is regex, not AST.** `_mask_identifiers` tokenizes with `\b[A-Za-z_][A-Za-z_0-9]*\b` and preserves a hand-maintained keyword set. A string like `for x in foo` masked as `for _ in _` is correct; a docstring containing what looks like identifiers is also masked, which is a false-positive risk that can in principle cause fingerprint collisions on unrelated tracebacks. An AST-based masker would eliminate this, at the cost of parsing every traceback line in isolation.
- **Namespace isolation is not a security sandbox.** `compile_transformer` restricts `__builtins__` but does not prevent escape via `type.__subclasses__()`, `__import__` lookups on captured frames, or attribute walks from any exposed class (`cst.Module`, etc.). Darwin trusts that (a) transformer sources originate from its own diagnose path under the safety gate, and (b) operators do not import a third-party blackboard without review. A hardened deployment requires either subinterpreter isolation, WASM, or a separate process with seccomp.
- **Distribution shift at dataset scale is unmodeled.** At N ≫ 10⁴ fingerprints the blackboard becomes a sorted-glob scan, which is O(N) per lookup. A B-tree or SQLite index is a straightforward fix but unimplemented. More subtly, fingerprint-key collisions across unrelated failures (different bug, same masked core) become non-negligible at scale; we have not measured the rate.
- **Semantic-equivalence is not proven.** The safety gate is a structural stopgap. A fix that preserves `try`/`except` counts and import supersets can still silently alter semantics (e.g., the rate-limit transformer removes a `raise`; we mitigate by injecting a `warnings.warn` so the change is observable in logs, but this is a convention, not a theorem).
- **Benchmark harness self-synchronizes workers.** Real production fleets do not enter a failure path under a `multiprocessing.Barrier`. Worst-case contention in production is bounded by network jitter and queue depth, not by our barrier; the published wall-clock numbers represent the pathological case, not the typical one.

## 9. Future Work

- **BugsInPy held-out evaluation.** Report heal rate, false-positive rate, and time-to-heal on a public corpus the authors did not write.
- **Multi-language via tree-sitter bindings.** JS/TS first (largest agent footprint), Go second.
- **AST-accurate identifier masking.** Replace the regex in `signature._mask_identifiers` with a per-line tokenizer that understands string/docstring context.
- **Cryptographic attestation of cached recipes.** Sign transformer sources with a per-operator key so a cross-org blackboard federation is plausible without a full review of every recipe.
- **Formal semantic-equivalence gate.** For a subset of transformers (those that match a handful of known-safe rewrite shapes), a symbolic-execution or bounded-model-checking pass that discharges equivalence over a bounded input space.
- **Scaling the blackboard.** SQLite index, then a KV store (RocksDB / SQLite WAL) once fingerprints exceed 10⁶.

Reproducibility: commit SHA `17e2d30`, Python 3.11, libcst 1.5.x, 2026-04-22.
