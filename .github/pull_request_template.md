## Summary

What does this PR change and why?

## Type of change

- [ ] Bug fix (non-breaking, fixes an issue)
- [ ] New feature (non-breaking, additive)
- [ ] Breaking change (fix or feature that changes existing behavior)
- [ ] Documentation only
- [ ] Tests only

## Related issues

Closes / refs #...

## How was this tested?

- [ ] `python -m pytest -q` passes locally
- [ ] `python scripts/smoke_test.py` passes (for user-facing changes)
- [ ] Manually verified: (describe platform, setup, what you exercised)

## Checklist

- [ ] New/changed behavior has regression tests (bug fixes: a test that failed before the fix)
- [ ] Schema changes use the additive migration pattern in `app/db.py` — existing installs never need to delete data or re-upload manuals
- [ ] No new mandatory dependency without discussion; optional extras go in their own requirements file
- [ ] Values/claims in UI or docs stay honest: no invented sources, no presenting cached content as live answers, unverified specs labeled as candidates
- [ ] Documentation updated (README, docs/, docstrings) where behavior or setup changed
- [ ] Bundled third-party assets are recorded in `THIRD_PARTY_NOTICES.md`
