### Summary
What this change does and why.

### Changes
-

### How it was tested
- [ ] Offline tests pass: `uv run pytest -m "not live" tests/v2`
- [ ] Live tests run, if the change touches how runs talk to a model (`HERCULES_LIVE_LLM=1 HERCULES_LIVE_JEV=1`)
- [ ] UI tests pass, if the change touches `ui/` (`npm test -- --run`)

### Checklist
- [ ] `make lint` passes (black, line length 200)
- [ ] Docs in `docs/guide/` and `README.md` match the change
- [ ] No step reading or decision is made by hand-written rules (see CONTRIBUTING.md)
- [ ] No secrets, keys, private hosts or real personal data in code, tests, fixtures or docs

Closes #
