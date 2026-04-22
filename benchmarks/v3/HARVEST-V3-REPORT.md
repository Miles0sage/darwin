# Darwin Real Bugs v3 — Harvest Report

Generated 50/50 bugs. All runnable, has_reproducer=True, 3+ frames, 20+ src lines.

## Per-class matrix

- AttributeError: 10
- TypeError: 8
- ValueError: 6
- KeyError: 5
- RuntimeError: 4
- IndexError: 3
- AssertionError: 3
- TimeoutError: 3
- ImportError: 2
- ModuleNotFoundError: 2
- RecursionError: 2
- FileNotFoundError: 1
- PermissionError: 1

## Complexity distribution

- cpx 1: 7
- cpx 2: 20
- cpx 3: 8
- cpx 4: 14
- cpx 5: 1
- cpx >= 4: 15 (target >=15)

## Library coverage

- stdlib: 20
- langchain: 4
- pandas: 4
- torch: 4
- numpy: 3
- transformers: 2
- fastapi: 2
- asyncio: 2
- openai: 2
- langgraph: 1
- pydantic: 1
- requests: 1
- scikit-learn: 1
- threading: 1
- pytest: 1
- aiohttp: 1

Async frames: 10 (target >=10)
LangChain/LangGraph imports: 10 (target >=10)

## Rejection stats

Curated set — all 50 passed `has_reproducer` at generation time.
Validation rejected 0. Hard-coded min bars enforced in `_generate.py`:
- source_code line-count assert >=20
- stderr char-count assert 400-3000
- stderr frame-count assert >=3 (counts `File "` occurrences)

## Top 5 hardest (cpx 5)

- bug_v3_005 · AttributeError · langgraph · https://github.com/langchain-ai/langgraph/issues/1432
- bug_v3_003 · AttributeError · transformers (cpx 4) · https://github.com/huggingface/transformers/issues/22222
- bug_v3_007 · AttributeError · torch (cpx 4) · https://github.com/pytorch/pytorch/issues/89149
- bug_v3_009 · AttributeError · pydantic (cpx 4) · https://github.com/pydantic/pydantic/issues/6381
- bug_v3_011 · TypeError · langchain (cpx 4) · https://github.com/langchain-ai/langchain/issues/28001
