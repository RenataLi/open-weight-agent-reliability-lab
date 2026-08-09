# Contributing

Contributions that strengthen agent evaluation, fault injection, provider compatibility, or
reproducibility are welcome. The project is developed in small, reviewable milestones: each change
should include the narrowest tests that prove its behavior and must not weaken a safety invariant.

## Development setup

The repository targets CPython 3.12 and uses a fully locked `uv` environment.


```powershell
uv sync --all-groups
uv run agent-reliability-lab validate
uv run agent-reliability-lab smoke
```

## Quality gate

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

The same gate runs in GitHub Actions. Pull requests should be focused, typed, tested, and free of
raw transcripts, model weights, credentials, or local run artifacts.

## Benchmark changes

When adding or changing a task:

1. explain the failure mode and expected safe behavior;
2. keep the world deterministic and offline;
3. add a reference trajectory;
4. verify exact final state and trace behavior;
5. increment the suite version if results are no longer comparable;
6. document any corrected task in `CHANGELOG.md`.

Never weaken a policy or invariant merely to make a trajectory pass.

## Pull requests

1. Open an issue for substantial benchmark or protocol changes.
2. Keep commits small and explain the observable behavior being changed.
3. Include test evidence and update documentation when public contracts move.
4. Call out any comparability break and bind new evidence to a new suite or protocol version.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
