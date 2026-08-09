from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from agent_reliability_lab.agent.models import AgentSuiteResult
from agent_reliability_lab.cli import (
    DEFAULT_STUDY_CELLS,
    _docker_model_provenance,
    _safe_container_environment,
    _safe_vllm_arguments,
    _sanitized_container_command_sha256,
    app,
)
from agent_reliability_lab.providers.base import ProviderError, ProviderErrorCode

runner = CliRunner()
ROOT = Path(__file__).parents[2]
SUITE = ROOT / "benchmarks" / "smoke_suite.json"
PROMPTS = ROOT / "benchmarks" / "model_prompts_v1.json"
STUDY_PROTOCOL = ROOT / "studies" / "m3_protocol.json"


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
        contract_version=prompt_set.contract_version,
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


def test_study_validate_and_plan_are_frozen_and_machine_readable(tmp_path: Path) -> None:
    validated = runner.invoke(app, ["study", "validate", str(STUDY_PROTOCOL)])
    assert validated.exit_code == 0
    assert "24 cells" in validated.stdout
    assert "160 scored + 32 descriptive" in validated.stdout
    assert "5dee1a3fc9f815a04a1c6ea06b4a52cb431831e062f649e54d01c5a4dc586b77" in (validated.stdout)
    assert Path("runs/m3-controlled-contract-study-v1r2/cells") == DEFAULT_STUDY_CELLS

    output = tmp_path / "study-plan.json"
    planned = runner.invoke(
        app,
        ["study", "plan", str(STUDY_PROTOCOL), "--output", str(output), "--json"],
    )
    assert planned.exit_code == 0
    payload = json.loads(planned.stdout)
    assert payload["study_id"] == "m3-controlled-contract-study-v1r2"
    assert payload["total_task_runs"] == 192
    assert len(payload["cells"]) == 24
    assert json.loads(output.read_text(encoding="utf-8")) == payload

    repeated = runner.invoke(
        app,
        ["study", "plan", str(STUDY_PROTOCOL), "--output", str(output)],
    )
    assert repeated.exit_code == 0


def test_study_plan_refuses_to_overwrite_different_artifact(tmp_path: Path) -> None:
    output = tmp_path / "study-plan.json"
    output.write_text("{}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["study", "plan", str(STUDY_PROTOCOL), "--output", str(output)],
    )

    assert result.exit_code == 2
    assert "refusing to overwrite" in result.stderr
    assert output.read_text(encoding="utf-8") == "{}\n"


def test_study_report_fails_closed_on_partial_matrix(tmp_path: Path) -> None:
    output = tmp_path / "aggregate"

    result = runner.invoke(
        app,
        [
            "study",
            "report",
            str(STUDY_PROTOCOL),
            "--cells-dir",
            str(tmp_path / "cells"),
            "--output-dir",
            str(output),
        ],
    )

    assert result.exit_code == 2
    assert "required study artifact is missing" in result.stderr
    assert not output.exists()


def test_study_validate_cell_rejects_unknown_or_missing_cell(tmp_path: Path) -> None:
    unknown = runner.invoke(
        app,
        [
            "study",
            "validate-cell",
            "m3s-not-in-plan-v1-s00000011-r01",
            "--protocol",
            str(STUDY_PROTOCOL),
            "--cells-dir",
            str(tmp_path),
        ],
    )
    assert unknown.exit_code == 2
    assert "not in the frozen plan" in unknown.stderr

    missing = runner.invoke(
        app,
        [
            "study",
            "validate-cell",
            "m3s-qwen35-9b-v1-s00000011-r01",
            "--protocol",
            str(STUDY_PROTOCOL),
            "--cells-dir",
            str(tmp_path),
        ],
    )
    assert missing.exit_code == 2
    assert "required study artifact is missing" in missing.stderr


def test_docker_command_provenance_allowlists_flags_without_secrets() -> None:
    command = json.dumps(
        [
            "--model",
            "Qwen/model",
            "--revision",
            "immutable-sha",
            "--tokenizer-revision",
            "immutable-tokenizer-sha",
            "--tokenizer-mode",
            "mistral",
            "--max-num-batched-tokens",
            "8192",
            "--seed",
            "0",
            "--api-key",
            "do-not-persist-me",
            "--language-model-only",
        ]
    )

    observed = _safe_vllm_arguments(command)

    assert observed == {
        "model": "Qwen/model",
        "revision": "immutable-sha",
        "tokenizer-revision": "immutable-tokenizer-sha",
        "tokenizer-mode": "mistral",
        "max-num-batched-tokens": "8192",
        "seed": "0",
        "language-model-only": True,
    }
    assert "do-not-persist-me" not in json.dumps(observed)


def test_container_environment_provenance_is_allowlisted_and_secret_free() -> None:
    environment = json.dumps(
        [
            "PATH=/usr/local/bin",
            "HF_TOKEN=do-not-persist-me",
            "VLLM_USE_V2_MODEL_RUNNER=0",
        ]
    )

    observed = _safe_container_environment(environment)

    assert observed == {"VLLM_USE_V2_MODEL_RUNNER": "0"}
    assert "do-not-persist-me" not in json.dumps(observed)
    assert _safe_container_environment("not-json") is None
    assert _safe_container_environment('["VLLM_USE_V2_MODEL_RUNNER=unexpected"]') is None


def test_docker_provenance_records_exact_safe_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = "agent-reliability-vllm"
    outputs = {
        ("docker", "inspect", "--format", "{{.Image}}", container): "sha256:image-id",
        ("docker", "inspect", "--format", "{{.Config.Image}}", container): "image@sha256:pin",
        ("docker", "inspect", "--format", "{{json .Config.Cmd}}", container): '["--model","m"]',
        ("docker", "inspect", "--format", "{{json .Config.Env}}", container): (
            '["HF_TOKEN=secret","VLLM_USE_V2_MODEL_RUNNER=0"]'
        ),
        (
            "docker",
            "image",
            "inspect",
            "sha256:image-id",
            "--format",
            "{{json .RepoDigests}}",
        ): '["image@sha256:pin"]',
    }
    monkeypatch.setattr(
        "agent_reliability_lab.cli._command_output",
        lambda arguments, **_: outputs.get(tuple(arguments)),
    )

    observed = _docker_model_provenance()

    assert observed["observed_container_environment"] == {"VLLM_USE_V2_MODEL_RUNNER": "0"}
    assert observed["observed_container_environment_sha256"] == (
        "5e33d65eeccd9fd14aecd7d1f7442bf47bc308b8449a7f9cfb616a21a9184560"
    )
    assert "secret" not in json.dumps(observed)


def test_complete_container_command_hash_redacts_secrets_and_binds_unknown_flags() -> None:
    first = json.dumps(
        [
            "--model",
            "model-a",
            "--api-key",
            "first-secret",
            "--future-behavior-flag=enabled",
            "--hf-token=another-secret",
        ]
    )
    rotated_secrets = json.dumps(
        [
            "--model",
            "model-a",
            "--api-key",
            "rotated-secret",
            "--future-behavior-flag=enabled",
            "--hf-token=rotated-token",
        ]
    )
    changed_behavior = json.dumps(
        [
            "--model",
            "model-a",
            "--api-key",
            "first-secret",
            "--future-behavior-flag=disabled",
            "--hf-token=another-secret",
        ]
    )

    first_digest = _sanitized_container_command_sha256(first)

    assert first_digest is not None and len(first_digest) == 64
    assert first_digest == _sanitized_container_command_sha256(rotated_secrets)
    assert first_digest != _sanitized_container_command_sha256(changed_behavior)
    assert _sanitized_container_command_sha256('["--api-key"]') is None


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


def test_agent_run_requires_paired_study_provenance(tmp_path: Path) -> None:
    output_dir = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            *_agent_args(output_dir),
            "--study-id",
            "m3-controlled-contract-study-v1r2",
        ],
    )

    assert result.exit_code == 2
    assert "study-id and study-protocol-sha256 must be supplied together" in result.stderr
    assert not output_dir.exists()


def test_agent_run_rejects_compose_outside_project_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    project_root = tmp_path / "project"
    project_root.mkdir()
    monkeypatch.setattr("agent_reliability_lab.cli._project_root", lambda: project_root)
    outside = tmp_path / "compose.yml"
    outside.write_text("services: {}\n", encoding="utf-8")
    output_dir = tmp_path / "runs"

    result = runner.invoke(
        app,
        [*_agent_args(output_dir), "--deployment-compose", str(outside)],
    )

    assert result.exit_code == 2
    assert "deployment compose must be inside the project directory" in result.stderr
    assert not _FakeProvider.instances
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
    assert manifest["contract_version"] == "v1"
    assert manifest["study_id"] is None
    assert manifest["study_protocol_sha256"] is None
    assert manifest["chat_template_mode"] == "disable_thinking"
    assert manifest["request_extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert manifest["request_extra_body_sha256"] == (
        "a708e0e8efb2d85fcfb6e63358670bd57378384f1fcb315391b2630de3d49e45"
    )
    assert manifest["deployment_compose_path"] == "deploy/compose.gpu.yml"
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


def test_agent_run_contract_override_is_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_reliability_lab.cli.OpenAICompatibleProvider", _FakeProvider)
    monkeypatch.setattr(
        "agent_reliability_lab.cli.run_agent_suite",
        lambda suite, prompt_set, provider, config, **kwargs: _passing_agent_result(
            suite=suite,
            prompt_set=prompt_set,
            provider=provider,
            config=config,
            **kwargs,
        ),
    )
    output_dir = tmp_path / "runs"

    result = runner.invoke(
        app,
        [
            *_agent_args(output_dir),
            "--model",
            "test-model",
            "--contract-version",
            "v2",
            "--study-id",
            "m3-controlled-contract-study-v1r2",
            "--study-protocol-sha256",
            "5dee1a3fc9f815a04a1c6ea06b4a52cb431831e062f649e54d01c5a4dc586b77",
            "--chat-template-mode",
            "native",
            "--run-id",
            "contract-v2",
        ],
    )

    assert result.exit_code == 0
    manifest = json.loads((output_dir / "contract-v2" / "manifest.json").read_text("utf-8"))
    result_payload = json.loads((output_dir / "contract-v2" / "result.json").read_text("utf-8"))
    assert manifest["contract_version"] == "v2"
    assert manifest["study_id"] == "m3-controlled-contract-study-v1r2"
    assert manifest["study_protocol_sha256"] == (
        "5dee1a3fc9f815a04a1c6ea06b4a52cb431831e062f649e54d01c5a4dc586b77"
    )
    assert manifest["chat_template_mode"] == "native"
    assert "chat_template_kwargs" not in manifest["request_extra_body"]
    assert manifest["request_extra_body_sha256"] == (
        "446637575e9a6dd899e6f42f507eb9c03ae16d3428fcfed938eda191d7ddc3c5"
    )
    assert result_payload["contract_version"] == "v2"
    assert manifest["system_prompt_sha256"] != (
        "4843d9ffe75650553e19b9117558685a38436f043a1c089bd8add8e5300e716e"
    )
