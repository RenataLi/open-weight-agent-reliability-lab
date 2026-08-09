from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_reliability_lab.study import (
    StudyArtifactError,
    build_study_plan,
    canonical_sha256,
    load_study_artifacts,
    load_study_cell_artifact,
    load_study_spec,
)
from agent_reliability_lab.study import artifacts as artifact_module
from agent_reliability_lab.study.plan import file_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _manifest(spec: object, cell: object) -> dict[str, object]:
    from agent_reliability_lab.study.models import StudyCell, StudySpec

    typed_spec = StudySpec.model_validate(spec)
    typed_cell = StudyCell.model_validate(cell)
    model = next(item for item in typed_spec.models if item.model_key == typed_cell.model_key)
    arguments = dict(model.expected_vllm_arguments)
    return {
        "run_id": typed_cell.cell_id,
        "study_id": typed_spec.study_id,
        "study_protocol_sha256": canonical_sha256(typed_spec),
        "suite_id": typed_spec.dataset.suite_id,
        "suite_sha256": typed_spec.dataset.suite_sha256,
        "prompt_set_id": typed_spec.dataset.prompt_set_id,
        "prompt_set_sha256": typed_spec.dataset.prompt_set_sha256,
        "contract_version": typed_cell.contract_version.value,
        "chat_template_mode": model.chat_template_mode.value,
        "request_extra_body": model.expected_request_extra_body,
        "request_extra_body_sha256": model.expected_request_extra_body_sha256,
        "system_prompt_sha256": typed_spec.system_prompt_sha256[typed_cell.contract_version],
        "tool_schemas_sha256": typed_spec.tool_schemas_sha256,
        "uv_lock_sha256": typed_spec.uv_lock_sha256,
        "model": typed_cell.served_model_name,
        "model_revision": typed_cell.model_revision,
        "declared_model_revision": typed_cell.model_revision,
        "policy": typed_spec.policy.value,
        "repetitions": 1,
        "provider_base_url": typed_spec.execution.provider_base_url,
        "api_key_present": typed_spec.execution.api_key_present,
        "seed": typed_cell.seed,
        "temperature": typed_cell.temperature,
        "top_p": typed_cell.top_p,
        "max_tokens": typed_spec.execution.max_tokens,
        "max_turns": typed_spec.execution.max_turns,
        "timeout_seconds": typed_spec.execution.timeout_seconds,
        "provider_attempts": typed_spec.execution.provider_attempts,
        "provider_backoff_seconds": typed_spec.execution.provider_backoff_seconds,
        "task_id": None,
        "model_identity_verified": True,
        "model_revision_observed_in_container_command": True,
        "observed_model_ids": [typed_cell.served_model_name],
        "vllm_image_reference": model.vllm_image_reference,
        "deployment_compose_path": model.deployment_compose,
        "deployment_compose_sha256": model.deployment_compose_sha256,
        "observed_container_image_reference": model.vllm_image_reference,
        "observed_container_command_sha256": model.expected_container_command_sha256,
        "observed_container_environment": model.expected_container_environment,
        "observed_container_environment_sha256": (model.expected_container_environment_sha256),
        "observed_vllm_arguments": arguments,
        "observed_vllm_arguments_sha256": canonical_sha256(arguments),
        "observed_vllm_version": typed_spec.vllm_version,
        "git_commit": "c" * 40,
        "git_dirty": False,
    }


def test_artifact_loader_rejects_missing_cell_directory(tmp_path: Path) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)

    with pytest.raises(StudyArtifactError, match="missing"):
        load_study_artifacts(spec, plan, tmp_path)


def test_artifact_loader_rejects_tampered_manifest_before_analysis(tmp_path: Path) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = plan.cells[0]
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    manifest = _manifest(spec, cell)
    manifest["seed"] = 999
    (cell_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cell_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StudyArtifactError, match=r"seed.*mismatch"):
        load_study_cell_artifact(spec, cell, tmp_path)


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("study_id", "m3-controlled-contract-study-v1"),
        ("study_protocol_sha256", "0" * 64),
        ("provider_base_url", "http://localhost:8000/v1"),
        ("max_turns", 13),
        ("provider_attempts", 2),
    ],
)
def test_artifact_loader_rejects_tampered_execution_parameters(
    tmp_path: Path,
    field: str,
    tampered: object,
) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = plan.cells[0]
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    manifest = _manifest(spec, cell)
    manifest[field] = tampered
    (cell_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cell_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StudyArtifactError, match=rf"{field!r} mismatch"):
        load_study_cell_artifact(spec, cell, tmp_path)


def test_artifact_loader_rejects_tampered_container_environment(tmp_path: Path) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = next(item for item in plan.cells if item.model_key == "ministral3-8b")
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    manifest = _manifest(spec, cell)
    manifest["observed_container_environment"] = {"VLLM_USE_V2_MODEL_RUNNER": "1"}
    (cell_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cell_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StudyArtifactError, match=r"observed_container_environment.*mismatch"):
        load_study_cell_artifact(spec, cell, tmp_path)


def test_artifact_loader_rejects_tampered_request_extra_body(tmp_path: Path) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = next(item for item in plan.cells if item.model_key == "ministral3-8b")
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    manifest = _manifest(spec, cell)
    manifest["request_extra_body"] = {
        "max_tokens": 2048,
        "top_p": 1.0,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "chat_template_kwargs": {"enable_thinking": False},
    }
    (cell_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cell_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StudyArtifactError, match=r"request_extra_body.*mismatch"):
        load_study_cell_artifact(spec, cell, tmp_path)


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("chat_template_mode", "disable_thinking"),
        ("request_extra_body_sha256", "0" * 64),
    ],
)
def test_artifact_loader_rejects_tampered_request_profile_metadata(
    tmp_path: Path,
    field: str,
    tampered: object,
) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = next(item for item in plan.cells if item.model_key == "ministral3-8b")
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    manifest = _manifest(spec, cell)
    manifest[field] = tampered
    (cell_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (cell_dir / "result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(StudyArtifactError, match=rf"{field}.*mismatch"):
        load_study_cell_artifact(spec, cell, tmp_path)


def test_artifact_loader_rejects_tampered_result_schema(tmp_path: Path) -> None:
    spec = load_study_spec(PROJECT_ROOT / "studies" / "m3_protocol.json")
    plan = build_study_plan(spec)
    cell = plan.cells[0]
    cell_dir = tmp_path / cell.cell_id
    cell_dir.mkdir()
    (cell_dir / "manifest.json").write_text(
        json.dumps(_manifest(spec, cell)),
        encoding="utf-8",
    )
    (cell_dir / "result.json").write_text(
        json.dumps({"model": "tampered-model"}),
        encoding="utf-8",
    )

    with pytest.raises(StudyArtifactError, match="invalid result schema"):
        load_study_cell_artifact(spec, cell, tmp_path)


def test_event_stream_is_nonempty_valid_jsonl_and_digest_bound(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text('{"kind":"model_turn"}\n{"kind":"tool_result"}\n', encoding="utf-8")

    first_digest = artifact_module._validate_events(events)
    assert first_digest == file_sha256(events)
    events.write_text(
        '{"kind":"model_turn"}\n{"kind":"tool_result"}\n{"kind":"end"}\n',
        encoding="utf-8",
    )
    assert artifact_module._validate_events(events) != first_digest

    events.write_text('{"valid":true}\nnot-json\n', encoding="utf-8")
    with pytest.raises(StudyArtifactError, match="invalid JSON event"):
        artifact_module._validate_events(events)
