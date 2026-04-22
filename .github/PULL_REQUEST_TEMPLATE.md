## Summary

Brief description of the change.

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature
- [ ] New benchmark corpus entry
- [ ] New failure class (transformer + pristine + test)
- [ ] Documentation

## For new failure classes

- [ ] Added `reference_recipe_for()` entry in `patch.py`
- [ ] Added pristine + expected-fix pair
- [ ] Extended `benchmark.py` or `benchmarks/`
- [ ] Re-ran no-gate baseline to confirm AST gate rejects wrong patches
- [ ] `pytest test_crossfeed.py test_triage.py` passes

## For bug fixes

- [ ] Existing tests pass: `pytest -p no:xdist --override-ini="addopts=" test_crossfeed.py test_triage.py`
- [ ] No hardcoded absolute paths added
- [ ] No API keys or secrets in diff

## Notes

Any context reviewers should know.
