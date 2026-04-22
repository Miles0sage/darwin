# real-bugs-v2 Harvest Report

**Total: 171 bugs** | Error classes: 43 (target: 20+)

## Per-class counts

| Class | N | Class | N |
|-------|---|-------|---|
| TypeError | 20 | RuntimeError | 19 |
| AttributeError | 17 | ValueError | 11 |
| KeyError | 9 | CancelledError | 7 |
| IndexError | 7 | AssertionError | 6 |
| RecursionError | 6 | ImportError | 6 |
| TimeoutError | 5 | IntegrityError | 5 |
| ModuleNotFoundError | 5 | PermissionError | 4 |
| UnicodeDecodeError | 4 | MemoryError | 3 |
| NameError | 2 | OSError | 2 |
| OverflowError | 2 | ZeroDivisionError | 2 |
| RayTaskError | 2 | InsecureRequestWarning | 2 |
| ValidationError | 2 | ArgumentError | 2 |
| CalledProcessError | 2 | FileNotFoundError | 2 |
| UserWarning | 1 | ConnectionError | 1 |
| ForeignKeyViolationError | 1 | CoverageWarning | 1 |
| InvalidUrlClientError | 1 | OperationalError | 1 |
| EnvironmentError | 1 | ClosedResourceError | 1 |
| AuthenticationError | 1 | SupervisorBadRequestError | 1 |
| HFValidationError | 1 | RegexMatchError | 1 |
| HTTPStatusError | 1 | LookupError | 1 |
| HTTPError | 1 | SSLWantReadError | 1 |
| PicklingError | 1 |  |  |

## Complexity

Scores: s2=2 | s3=7 | s4=24 | s5=138. All stderr >=200 chars. 150/171 >=500 chars. 142/171 have >=4 frames. 149/171 repros >=20 lines.

## Top 5 hardest bugs

- bug_v2_003 (AttributeError, s5): [Bug]: `_collection` attribute of `ChromaVectorStore` not co — 19-frame,multi-file,lib:llama_index
- bug_v2_010 (TypeError, s5): Latest openai package fails with TypeError: Cannot instantia — 13-frame,async,multi-file,lib:pydantic-ai
- bug_v2_001 (TimeoutError, s5): Redis pubsub timeout when waiting for multiple results in th — 19-frame,async,multi-file,lib:celery
- bug_v2_005 (AttributeError, s5): [Bug]: Vertex LLM does not Handle FunctionCall tools — 13-frame,async,multi-file,lib:llama_index
- bug_v2_008 (AttributeError, s5): Race condition Airflow's Celery executor timeout and import  — 17-frame,async,multi-file,lib:airflow

## Rejection stats

- GitHub candidates w/ Traceback: 258 | per-class cap reject: ~90 | final kept: 143 real + 28 crafted.

## Source yield (top 10)

stackoverflow.com:28 | home-assistant/core:15 | langchain-ai/langchain:5 | run-llama/llama_index:4 | huggingface/transformers:4 | pytorch/pytorch:4 | pandas-dev/pandas:3 | ray-project/ray:3 | BerriAI/litellm:3 | pypa/pip:3

## Corpus quality verdict

Strong: 43 error classes, 162/171 at complexity >=4. Multi-frame tracebacks dominate (142 with >=4 frames; 107 with >=6). Covers async/asyncio (63), threading/deadlock/race (50), type-system edges (34), agent frameworks (18), data-science (44). All 11 hard quotas PASS. Biased heavily toward real production failures from home-assistant, langchain, ray, pytorch, transformers, pandas, celery, sqlalchemy — not toy tracebacks.