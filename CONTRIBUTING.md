# Contributing

The project is developed in small, reviewable milestones. A change should include the narrowest
tests that prove its behavior and must not weaken a safety invariant.

## Local checks

```powershell
uv sync --all-groups
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

## Benchmark changes

When adding or changing a task:

1. explain the failure mode and expected safe behavior;
2. keep the world deterministic and offline;
3. add a reference trajectory;
4. verify exact final state and trace behavior;
5. increment the suite version if results are no longer comparable;
6. document any corrected task in `CHANGELOG.md`.

Never weaken a policy or invariant merely to make a trajectory pass.
