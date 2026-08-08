"""Public command line interface for the offline benchmark core."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from agent_reliability_lab.tasks.evaluator import load_suite, run_suite, validate_suite_contract
from agent_reliability_lab.tasks.models import BenchmarkSuite
from agent_reliability_lab.tools.registry import build_registry

app = typer.Typer(
    name="agent-reliability-lab",
    help="Deterministic reliability benchmark for open-weight tool-calling agents.",
    no_args_is_help=True,
)

DEFAULT_SUITE = Path("benchmarks/smoke_suite.json")


def _load_or_exit(path: Path) -> BenchmarkSuite:
    try:
        return load_suite(path)
    except FileNotFoundError:
        typer.echo(f"Benchmark file not found: {path}", err=True)
        raise typer.Exit(2) from None
    except (OSError, ValidationError, ValueError) as exc:
        typer.echo(f"Invalid benchmark: {exc}", err=True)
        raise typer.Exit(2) from None


@app.command()
def validate(
    path: Annotated[
        Path, typer.Argument(help="Path to a benchmark suite JSON file.")
    ] = DEFAULT_SUITE,
) -> None:
    """Validate schemas, references, tools, fixtures, and invariants."""

    suite = _load_or_exit(path)
    try:
        validate_suite_contract(suite)
    except ValueError as exc:
        typer.echo(f"Invalid benchmark contract: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(f"Validated {suite.suite_id}: {len(suite.tasks)} tasks")


@app.command()
def smoke(
    path: Annotated[
        Path, typer.Argument(help="Path to the smoke suite JSON file.")
    ] = DEFAULT_SUITE,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit only the machine-readable result."),
    ] = False,
) -> None:
    """Run reference trajectories and exact deterministic evaluation."""

    suite = _load_or_exit(path)
    try:
        result = run_suite(suite)
    except ValueError as exc:
        typer.echo(f"Invalid benchmark contract: {exc}", err=True)
        raise typer.Exit(2) from None
    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    else:
        typer.echo(f"{result.suite_id}: {result.passed_tasks}/{result.total_tasks} tasks passed")
        for task in result.tasks:
            marker = "PASS" if task.passed else "FAIL"
            typer.echo(f"  [{marker}] {task.task_id}")
            if not task.passed:
                for check in task.checks:
                    if not check.passed:
                        typer.echo(
                            f"    {check.name}: expected={check.expected!r} "
                            f"observed={check.observed!r}"
                        )
    if not result.passed:
        raise typer.Exit(1)


@app.command("tool-schemas")
def tool_schemas() -> None:
    """Print stable input schemas for every simulator tool."""

    registry = build_registry()
    schemas = [registry[name].json_schema() for name in sorted(registry)]
    typer.echo(json.dumps(schemas, ensure_ascii=False, sort_keys=True))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
