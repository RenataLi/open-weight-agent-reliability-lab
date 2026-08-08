from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_reliability_lab.cli import app

runner = CliRunner()
ROOT = Path(__file__).parents[2]
SUITE = ROOT / "benchmarks" / "smoke_suite.json"


def test_help_contract() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Deterministic reliability benchmark" in result.stdout


def test_validate_contract() -> None:
    result = runner.invoke(app, ["validate", str(SUITE)])
    assert result.exit_code == 0
    assert "12 tasks" in result.stdout


def test_smoke_human_output() -> None:
    result = runner.invoke(app, ["smoke", str(SUITE)])
    assert result.exit_code == 0
    assert "12/12 tasks passed" in result.stdout
    assert result.stdout.count("[PASS]") == 12


def test_smoke_json_stdout_is_machine_readable() -> None:
    result = runner.invoke(app, ["smoke", str(SUITE), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["passed_tasks"] == 12
    assert payload["total_tasks"] == 12


def test_missing_file_returns_usage_error_without_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    result = runner.invoke(app, ["validate", str(missing)])
    assert result.exit_code == 2
    assert "not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_json_returns_schema_error(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version":"1.0"}', encoding="utf-8")
    result = runner.invoke(app, ["validate", str(invalid)])
    assert result.exit_code == 2
    assert "Invalid benchmark" in result.stderr


def test_tool_schemas_are_machine_readable() -> None:
    result = runner.invoke(app, ["tool-schemas"])
    assert result.exit_code == 0
    schemas = json.loads(result.stdout)
    assert len(schemas) == 12
    assert {schema["name"] for schema in schemas} >= {
        "rollback_deployment",
        "search_runbooks",
        "submit_incident_report",
    }


def test_failed_benchmark_returns_one_and_prints_failed_check(tmp_path: Path) -> None:
    payload = json.loads(SUITE.read_text(encoding="utf-8"))
    payload["tasks"][0]["expected"]["active_deployments"]["ranker-prod"] = "wrong"
    failing = tmp_path / "failing.json"
    failing.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(app, ["smoke", str(failing)])
    assert result.exit_code == 1
    assert "[FAIL] S01_BAD_DEPLOY_DIAGNOSIS" in result.stdout
    assert "active_deployments" in result.stdout
