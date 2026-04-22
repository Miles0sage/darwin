# Contributing to Darwin

## Adding Benchmark Bugs

1. Create bug file in `benchmarks/vN/bugs/bug_NNN.py`
2. Include metadata JSON:
```json
{
  "id": "bug_NNN",
  "error_class": "ValueError",
  "library": "langchain",
  "description": "Short description",
  "source": "GitHub issue #123 / production incident"
}
```
3. Bug must be reproducible: `python bug_NNN.py` triggers error
4. Ensure error message is clear (helps with diagnosis)

## AST Gate Philosophy

All patches MUST preserve syntax via LibCST round-trip:
- NO regex substitution
- NO string replacement
- ONLY AST node transformation + LibCST write-back
- Verify: `roundtrip(parse(original)) == original`

This ensures patches are semantically correct and don't introduce subtle bugs.

## Test Requirements

- 80%+ code coverage required
- Tests must run in <30 seconds
- Use pytest: `pytest tests/ -v`
- New features require test file in `tests/test_<feature>.py`

## Security

Report vulnerabilities to: amit.shah.5201@gmail.com
Do NOT open public issues for security bugs.

## Running Locally

```bash
python -m venv venv
source venv/bin/activate
pip install -e .
pytest tests/
python -m darwin.benchmark --corpus benchmarks/v3
```

## Submitting Changes

1. Fork the repository
2. Create feature branch: `git checkout -b fix/your-fix`
3. Commit with clear message: `fix: description`
4. Push and open PR with description of changes
5. Ensure tests pass before requesting review
