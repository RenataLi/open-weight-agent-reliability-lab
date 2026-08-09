"""Fail-closed loading of per-cell M3 run artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_reliability_lab.agent.models import AgentSuiteResult
from agent_reliability_lab.study.models import CellArtifact, StudyCell, StudyPlan, StudySpec
from agent_reliability_lab.study.plan import canonical_sha256, file_sha256


class StudyArtifactError(ValueError):
    """A cell artifact is absent, malformed, incomplete, or mismatched."""


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StudyArtifactError(f"required study artifact is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudyArtifactError(f"cannot read study artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StudyArtifactError(f"study artifact must be a JSON object: {path}")
    return value


def _validate_events(path: Path) -> str:
    if not path.is_file():
        raise StudyArtifactError(f"required study artifact is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StudyArtifactError(f"cannot read study event stream {path}: {exc}") from exc
    if not lines:
        raise StudyArtifactError(f"study event stream must not be empty: {path}")
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise StudyArtifactError(f"blank event at {path}:{line_number}")
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StudyArtifactError(f"invalid JSON event at {path}:{line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise StudyArtifactError(f"event must be a JSON object at {path}:{line_number}")
    return file_sha256(path)


def _expect(manifest: dict[str, Any], key: str, expected: object, cell_id: str) -> None:
    observed = manifest.get(key)
    if observed != expected:
        raise StudyArtifactError(
            f"cell {cell_id} manifest field {key!r} mismatch: "
            f"expected {expected!r}, observed {observed!r}"
        )


def _validate_manifest(
    manifest: dict[str, Any],
    cell: StudyCell,
    spec: StudySpec,
) -> None:
    expected: dict[str, object] = {
        "run_id": cell.cell_id,
        "suite_id": spec.dataset.suite_id,
        "suite_sha256": spec.dataset.suite_sha256,
        "prompt_set_id": spec.dataset.prompt_set_id,
        "prompt_set_sha256": spec.dataset.prompt_set_sha256,
        "contract_version": cell.contract_version.value,
        "system_prompt_sha256": spec.system_prompt_sha256[cell.contract_version],
        "tool_schemas_sha256": spec.tool_schemas_sha256,
        "uv_lock_sha256": spec.uv_lock_sha256,
        "model": cell.served_model_name,
        "model_revision": cell.model_revision,
        "declared_model_revision": cell.model_revision,
        "policy": spec.policy.value,
        "repetitions": cell.repetitions,
        "provider_base_url": spec.execution.provider_base_url,
        "api_key_present": spec.execution.api_key_present,
        "seed": cell.seed,
        "temperature": cell.temperature,
        "top_p": cell.top_p,
        "max_tokens": spec.execution.max_tokens,
        "max_turns": spec.execution.max_turns,
        "timeout_seconds": spec.execution.timeout_seconds,
        "provider_attempts": spec.execution.provider_attempts,
        "provider_backoff_seconds": spec.execution.provider_backoff_seconds,
        "task_id": None,
        "model_identity_verified": True,
        "model_revision_observed_in_container_command": True,
        "observed_vllm_version": spec.vllm_version,
        "git_dirty": False,
    }
    for key, value in expected.items():
        _expect(manifest, key, value, cell.cell_id)
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str) or re.fullmatch(r"[0-9a-f]{40}", git_commit) is None:
        raise StudyArtifactError(f"cell {cell.cell_id} lacks an immutable git commit")
    observed_model_ids = manifest.get("observed_model_ids")
    if not isinstance(observed_model_ids, list) or cell.served_model_name not in observed_model_ids:
        raise StudyArtifactError(
            f"cell {cell.cell_id} did not observe its pinned served model identity"
        )
    image_reference = manifest.get("vllm_image_reference")
    model_spec = next(model for model in spec.models if model.model_key == cell.model_key)
    if image_reference != model_spec.vllm_image_reference:
        raise StudyArtifactError(f"cell {cell.cell_id} used an unpinned vLLM image")
    _expect(
        manifest,
        "deployment_compose_path",
        model_spec.deployment_compose,
        cell.cell_id,
    )
    _expect(
        manifest,
        "deployment_compose_sha256",
        model_spec.deployment_compose_sha256,
        cell.cell_id,
    )
    _expect(
        manifest,
        "observed_container_image_reference",
        model_spec.vllm_image_reference,
        cell.cell_id,
    )
    _expect(
        manifest,
        "observed_container_command_sha256",
        model_spec.expected_container_command_sha256,
        cell.cell_id,
    )
    _expect(
        manifest,
        "observed_container_environment",
        model_spec.expected_container_environment,
        cell.cell_id,
    )
    _expect(
        manifest,
        "observed_container_environment_sha256",
        model_spec.expected_container_environment_sha256,
        cell.cell_id,
    )
    observed_arguments = manifest.get("observed_vllm_arguments")
    if not isinstance(observed_arguments, dict):
        raise StudyArtifactError(f"cell {cell.cell_id} lacks observed vLLM arguments")
    if observed_arguments != model_spec.expected_vllm_arguments:
        raise StudyArtifactError(
            f"cell {cell.cell_id} observed vLLM arguments differ from the exact pin"
        )
    _expect(
        manifest,
        "observed_vllm_arguments_sha256",
        model_spec.expected_vllm_arguments_sha256,
        cell.cell_id,
    )


def _validate_result(result: AgentSuiteResult, cell: StudyCell, spec: StudySpec) -> None:
    expected: tuple[tuple[str, object, object], ...] = (
        ("suite_id", spec.dataset.suite_id, result.suite_id),
        ("prompt_set_id", spec.dataset.prompt_set_id, result.prompt_set_id),
        ("contract_version", cell.contract_version, result.contract_version),
        ("model", cell.served_model_name, result.model),
        ("model_revision", cell.model_revision, result.model_revision),
        ("policy", spec.policy, result.policy),
        ("provider_base_url", spec.execution.provider_base_url, result.provider_base_url),
        ("repetitions", cell.repetitions, result.repetitions),
        ("expected_runs", len(cell.task_ids), result.expected_runs),
        ("total_runs", len(cell.task_ids), result.total_runs),
        ("aborted", False, result.aborted),
    )
    for name, wanted, observed in expected:
        if wanted != observed:
            raise StudyArtifactError(
                f"cell {cell.cell_id} result field {name!r} mismatch: "
                f"expected {wanted!r}, observed {observed!r}"
            )
    observed_keys = [(item.task_id, item.repetition) for item in result.task_results]
    expected_keys = [(task_id, 1) for task_id in cell.task_ids]
    if sorted(observed_keys) != sorted(expected_keys):
        raise StudyArtifactError(
            f"cell {cell.cell_id} task/repetition keys do not match the frozen panel"
        )
    actual_passes = sum(item.passed for item in result.task_results)
    if result.passed_runs != actual_passes:
        raise StudyArtifactError(f"cell {cell.cell_id} passed_runs is internally inconsistent")
    for task in result.task_results:
        derived_pass = (
            all(check.passed for check in task.checks) and task.infrastructure_error is None
        )
        if task.passed is not derived_pass:
            raise StudyArtifactError(
                f"cell {cell.cell_id} task {task.task_id} pass flag is internally inconsistent"
            )
    if result.passed is not (actual_passes == len(cell.task_ids)):
        raise StudyArtifactError(f"cell {cell.cell_id} suite pass flag is internally inconsistent")


def load_study_artifacts(
    spec: StudySpec,
    plan: StudyPlan,
    artifacts_root: Path,
) -> tuple[CellArtifact, ...]:
    """Load every planned cell and reject missing, malformed, or mismatched evidence."""

    if plan.study_id != spec.study_id or plan.protocol_sha256 != canonical_sha256(spec):
        raise StudyArtifactError("study plan does not belong to the supplied protocol")
    return tuple(load_study_cell_artifact(spec, cell, artifacts_root) for cell in plan.cells)


def load_study_cell_artifact(
    spec: StudySpec,
    cell: StudyCell,
    artifacts_root: Path,
) -> CellArtifact:
    """Validate and load one cell for resume-safe orchestration."""

    cell_dir = artifacts_root / cell.cell_id
    manifest_path = cell_dir / "manifest.json"
    events_path = cell_dir / "events.jsonl"
    result_path = cell_dir / "result.json"
    manifest = _read_object(manifest_path)
    result_object = _read_object(result_path)
    _validate_manifest(manifest, cell, spec)
    try:
        result = AgentSuiteResult.model_validate(result_object)
    except ValidationError as exc:
        raise StudyArtifactError(
            f"cell {cell.cell_id} has an invalid result schema: {exc}"
        ) from exc
    _validate_result(result, cell, spec)
    return CellArtifact(
        cell=cell,
        git_commit=str(manifest["git_commit"]),
        manifest_sha256=file_sha256(manifest_path),
        events_sha256=_validate_events(events_path),
        result_sha256=file_sha256(result_path),
        result=result,
    )
