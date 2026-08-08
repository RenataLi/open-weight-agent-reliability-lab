from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from agent_reliability_lab.agent.models import AgentSuiteResult
from agent_reliability_lab.cli import _safe_vllm_arguments, app
from agent_reliability_lab.providers.base import ProviderError, ProviderErrorCode

runner = CliRunner()
ROOT = Path(__file__).parents[2]
SUITE = ROOT / "benchmarks" / "smoke_suite.json"
PROMPTS = ROOT / "benchmarks" / "model_prompts_v1.json"


class _FakeProvider:
    instances: ClassVar[list[_FakeProvider]] = []
    preflight_error: ClassVar[ProviderError | None] = None
    observed_models: ClassVar[tuple[str, ...]] = ("test-model",)

    def __init__(self, **kwargs: Any) -> None:
        self.model = str(kwargs["model"])
        self.closed = False
        self.preflight_calls = 0
        self.instances.append(self)

    def check_model_available(self) -> tuple[str, ...]:
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        return self.observed_models

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_provider() -> None:
    _FakeProvider.instances.clear()
    _FakeProvider.preflight_error = None
    _FakeProvider.observed_models = ("test-model",)


def _agent_args(output_dir: Path) -> list[str]:
    return [
        "agent",
        "run",
        str(SUITE),
        "--prompts",
        str(PROMPTS),
        "--output-dir",
        str(output_dir),
    ]


def _passing_agent_result(**kwargs: Any) -> AgentSuiteResult:
    suite = kwargs["suite"]
    prompt_set = kwargs["prompt_set"]
    provider = kwargs["provider"]
    config = kwargs["config"]
    return AgentSuiteResult(
        suite_id=suite.suite_id,
        prompt_set_id=prompt_set.prompt_set_id,
        model=provider.model,
        provider_base_url=kwargs["provider_base_url"],
        policy=config.policy,
        repetitions=kwargs["repetitions"],
        passed=True,
        passed_runs=1,
        total_runs=1,
        expected_runs=1,
        task_results=(),
        aggregate={"pass_rate": 1.0, "infrastructure_failures": 0},
    )


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


def test_docker_command_provenance_allowlists_flags_without_secrets() -> None:
    command = json.dumps(
        [
            "--model",
            "Qwen/model",
            "--revision",
            "immutable-sha",
            "--api-key",
            "do-not-persist-me",
            "--language-model-only",
        ]
    )

    observed = _safe_vllm_arguments(command)

    assert observed == {
        "model": "Qwen/model",
        "revision": "immutable-sha",
        "language-model-only": True,
    }
    assert "do-not-persist-me" not in json.dumps(observed)


def test_failed_benchmark_returns_one_and_prints_failed_check(tmp_path: Path) -> None:
    payload = json.loads(SUITE.read_text(encoding="utf-8"))
    payload["tasks"][0]["expected"]["active_deployments"]["ranker-prod"] = "wrong"
    failing = tmp_path / "failing.json"
    failing.write_text(json.dumps(payload), encoding="utf-8")
    result = runner.invoke(app, ["smoke", str(failing)])
    assert result.exit_code == 1
    assert "[FAIL] S01_BAD_DEPLOY_DIAGNOSIS" in result.stdout
    assert "active_deployments" in result.stdout


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://127.0.0.1/v1",
        "http:///v1",
        "http://user:secret@127.0.0.1/v1",
        "http://127.0.0.1/v1?token=secret",
        "http://127.0.0.1/v1#fragment",
        "http://127.0.0.1:not-a-port/v1",
        "http://bad host/v1",
        "http://a..b/v1",
        "http://127.0.0.1\\evil/v1",
    ],
)
def test_agent_run_rejects_unsafe_base_url_without_artifacts(
    tmp_path: Path,
    base_url: str,
) -> None:
    output_dir = tmp_path / "runs"
    result = runner.invoke(app, [*_agent_args(output_dir), "--base-url", base_url])
    assert result.exit_code == 2
    assert "Invalid agent run configuration" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_dir.exists()


def test_agent_run_rejects_blank_model_without_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs"
    result = runner.invoke(app, [*_agent_args(output_dir), "--model", "   "])
    assert result.exit_code == 2
    assert "model must not be empty" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_dir.exists()


def test_agent_run_rejects_unsafe_run_id_before_provider_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    output_dir = tmp_path / "runs"
    result = runner.invoke(app, [*_agent_args(output_dir), "--run-id", "../escape"])
    assert result.exit_code == 2
    assert "run_id" in result.stderr
    assert "Traceback" not in result.stderr
    assert not _FakeProvider.instances
    assert not output_dir.exists()


def test_agent_run_reports_model_preflight_failure_without_partial_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    _FakeProvider.preflight_error = ProviderError(
        ProviderErrorCode.TRANSPORT_ERROR,
        "model server unavailable",
        retryable=True,
    )
    output_dir = tmp_path / "runs"
    result = runner.invoke(
        app,
        [*_agent_args(output_dir), "--model", "test-model", "--run-id", "preflight-fails"],
    )
    assert result.exit_code == 3
    assert "Model preflight failed (transport_error)" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_dir.exists()
    assert len(_FakeProvider.instances) == 1
    assert _FakeProvider.instances[0].preflight_calls == 1
    assert _FakeProvider.instances[0].closed is True


def test_agent_run_rejects_unknown_canary_task_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    output_dir = tmp_path / "runs"
    result = runner.invoke(
        app,
        [*_agent_args(output_dir), "--task-id", "S99_UNKNOWN", "--run-id", "unknown-task"],
    )
    assert result.exit_code == 2
    assert "unknown task-id: S99_UNKNOWN" in result.stderr
    assert "Traceback" not in result.stderr
    assert not _FakeProvider.instances
    assert not output_dir.exists()


def test_agent_run_filters_canary_and_persists_preflight_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_run(
        suite: Any,
        prompt_set: Any,
        provider: Any,
        config: Any,
        **kwargs: Any,
    ) -> AgentSuiteResult:
        kwargs.update(
            suite=suite,
            prompt_set=prompt_set,
            provider=provider,
            config=config,
        )
        captured.update(kwargs)
        return _passing_agent_result(**kwargs)

    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setattr("agent_reliability_lab.cli.run_agent_suite", capture_run)
    _FakeProvider.observed_models = ("test-model", "embedding-model")
    output_dir = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            *_agent_args(output_dir),
            "--base-url",
            "http://127.0.0.1:8000/v1/",
            "--model",
            "test-model",
            "--task-id",
            "S01_BAD_DEPLOY_DIAGNOSIS",
            "--run-id",
            "canary",
        ],
    )
    assert result.exit_code == 0
    assert "1/1 runs passed" in result.stdout
    suite = captured["suite"]
    prompt_set = captured["prompt_set"]
    assert [task.task_id for task in suite.tasks] == ["S01_BAD_DEPLOY_DIAGNOSIS"]
    assert list(prompt_set.tasks) == ["S01_BAD_DEPLOY_DIAGNOSIS"]
    assert captured["provider_base_url"] == "http://127.0.0.1:8000/v1"

    manifest = json.loads((output_dir / "canary" / "manifest.json").read_text("utf-8"))
    assert manifest["task_id"] == "S01_BAD_DEPLOY_DIAGNOSIS"
    assert manifest["observed_model_ids"] == ["test-model", "embedding-model"]
    assert manifest["model_identity_verified"] is True
    assert manifest["provider_base_url"] == "http://127.0.0.1:8000/v1"
    assert manifest["timeout_seconds"] == 300.0
    assert manifest["provider_attempts"] == 2
    assert manifest["provider_backoff_seconds"] == 0.25
    assert "@sha256:" in manifest["vllm_image_reference"]
    assert len(manifest["deployment_compose_sha256"]) == 64
    for field in (
        "suite_sha256",
        "prompt_set_sha256",
        "system_prompt_sha256",
        "tool_schemas_sha256",
        "uv_lock_sha256",
    ):
        assert len(manifest[field]) == 64
    assert len(_FakeProvider.instances) == 1
    assert _FakeProvider.instances[0].preflight_calls == 1
    assert _FakeProvider.instances[0].closed is True
