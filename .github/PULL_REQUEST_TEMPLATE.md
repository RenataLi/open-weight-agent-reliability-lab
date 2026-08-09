## Summary

Describe the behavior or evidence this change adds.

## Validation

- [ ] `uv run ruff format --check src tests`
- [ ] `uv run ruff check src tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest`

## Reliability contract

- [ ] Safety and policy invariants are unchanged or explicitly documented.
- [ ] Benchmark changes include a reference trajectory and exact outcome checks.
- [ ] Comparable results remain bound to immutable inputs and revisions.
- [ ] No credentials, private traces, checkpoints, or raw run artifacts are included.
