"""Canonical study protocol loading and deterministic matrix expansion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent_reliability_lab.agent.models import AgentPromptSet
from agent_reliability_lab.agent.prompting import system_prompt
from agent_reliability_lab.study.models import StudyCell, StudyPhase, StudyPlan, StudySpec
from agent_reliability_lab.tasks.models import BenchmarkSuite
from agent_reliability_lab.tools.registry import build_registry


def _jsonable(value: BaseModel | object) -> object:
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def canonical_json(value: BaseModel | object) -> str:
    """Serialize an object in the canonical form used by all M3 digests."""

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: BaseModel | object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_study_spec(path: Path) -> StudySpec:
    return StudySpec.model_validate_json(path.read_text(encoding="utf-8"))


def verify_study_inputs(spec: StudySpec, project_root: Path) -> None:
    """Verify the frozen suite and prompts against their preregistered byte digests."""

    suite_path = project_root / Path(spec.dataset.suite_path)
    prompt_path = project_root / Path(spec.dataset.prompt_set_path)
    for path, expected_digest in (
        (suite_path, spec.dataset.suite_sha256),
        (prompt_path, spec.dataset.prompt_set_sha256),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"preregistered input is missing: {path}")
        observed = file_sha256(path)
        if observed != expected_digest:
            raise ValueError(
                f"preregistered input digest mismatch for {path}: "
                f"expected {expected_digest}, observed {observed}"
            )

    suite = BenchmarkSuite.model_validate_json(suite_path.read_text(encoding="utf-8"))
    prompts = AgentPromptSet.model_validate_json(prompt_path.read_text(encoding="utf-8"))
    if suite.suite_id != spec.dataset.suite_id:
        raise ValueError("suite_id does not match the preregistered protocol")
    if prompts.prompt_set_id != spec.dataset.prompt_set_id:
        raise ValueError("prompt_set_id does not match the preregistered protocol")
    if prompts.suite_id != suite.suite_id:
        raise ValueError("prompt set references a different suite")
    suite_task_ids = tuple(sorted(task.task_id for task in suite.tasks))
    prompt_task_ids = tuple(sorted(prompts.tasks))
    if suite_task_ids != spec.dataset.task_ids or prompt_task_ids != spec.dataset.task_ids:
        raise ValueError("suite or prompt task IDs do not match the preregistered panel")

    for contract in spec.contracts:
        observed = canonical_sha256(system_prompt(spec.policy, contract))
        if observed != spec.system_prompt_sha256[contract]:
            raise ValueError(f"system prompt hash changed for contract {contract.value}")
    schemas = [build_registry()[name].json_schema() for name in sorted(build_registry())]
    if canonical_sha256(schemas) != spec.tool_schemas_sha256:
        raise ValueError("tool schema hash changed after protocol registration")

    uv_lock = project_root / "uv.lock"
    if not uv_lock.is_file() or file_sha256(uv_lock) != spec.uv_lock_sha256:
        raise ValueError("uv.lock does not match the preregistered protocol")
    for model in spec.models:
        compose_path = project_root / model.deployment_compose
        if not compose_path.is_file():
            raise FileNotFoundError(f"pinned deployment compose is missing: {compose_path}")
        if file_sha256(compose_path) != model.deployment_compose_sha256:
            raise ValueError(f"deployment compose hash changed for {model.model_key}")
        compose_text = compose_path.read_text(encoding="utf-8")
        expected_fragments = (
            model.vllm_image_reference,
            model.model_id,
            model.revision,
            model.served_model_name,
            model.tool_call_parser,
        )
        if any(fragment not in compose_text for fragment in expected_fragments):
            raise ValueError(f"deployment compose identity mismatch for {model.model_key}")
        if model.tokenizer_revision is not None and model.tokenizer_revision not in compose_text:
            raise ValueError(f"tokenizer revision is not pinned for {model.model_key}")
        if canonical_sha256(model.expected_vllm_arguments) != (
            model.expected_vllm_arguments_sha256
        ):
            raise ValueError(f"safe vLLM argument hash changed for {model.model_key}")
        if canonical_sha256(model.expected_container_environment) != (
            model.expected_container_environment_sha256
        ):
            raise ValueError(f"safe container environment hash changed for {model.model_key}")
        for name, value in model.expected_container_environment.items():
            if f'{name}: "{value}"' not in compose_text:
                raise ValueError(
                    f"container environment is not pinned in Compose for {model.model_key}"
                )


def _cell_id(
    phase: StudyPhase,
    model_key: str,
    contract: str,
    seed: int,
) -> str:
    prefix = "m3s" if phase is StudyPhase.STOCHASTIC else "m3g"
    return f"{prefix}-{model_key}-{contract}-s{seed:08d}-r01"


def build_study_plan(spec: StudySpec) -> StudyPlan:
    """Expand the frozen Cartesian product in a stable, reviewable order."""

    cells: list[StudyCell] = []
    for model in spec.models:
        for seed in spec.stochastic.seeds:
            for contract in spec.contracts:
                cells.append(
                    StudyCell(
                        cell_id=_cell_id(
                            StudyPhase.STOCHASTIC,
                            model.model_key,
                            contract.value,
                            seed,
                        ),
                        phase=StudyPhase.STOCHASTIC,
                        model_key=model.model_key,
                        model_id=model.model_id,
                        served_model_name=model.served_model_name,
                        model_revision=model.revision,
                        contract_version=contract,
                        seed=seed,
                        temperature=spec.stochastic.temperature,
                        top_p=spec.stochastic.top_p,
                        repetitions=1,
                        task_ids=spec.dataset.task_ids,
                        promotion_gates_eligible=True,
                    )
                )
    if spec.greedy_anchor.enabled:
        for model in spec.models:
            for contract in spec.contracts:
                cells.append(
                    StudyCell(
                        cell_id=_cell_id(
                            StudyPhase.GREEDY_ANCHOR,
                            model.model_key,
                            contract.value,
                            spec.greedy_anchor.seed,
                        ),
                        phase=StudyPhase.GREEDY_ANCHOR,
                        model_key=model.model_key,
                        model_id=model.model_id,
                        served_model_name=model.served_model_name,
                        model_revision=model.revision,
                        contract_version=contract,
                        seed=spec.greedy_anchor.seed,
                        temperature=spec.greedy_anchor.temperature,
                        top_p=spec.greedy_anchor.top_p,
                        repetitions=1,
                        task_ids=spec.dataset.task_ids,
                        promotion_gates_eligible=False,
                    )
                )
    return StudyPlan(
        study_id=spec.study_id,
        protocol_sha256=canonical_sha256(spec),
        cells=tuple(cells),
        stochastic_cells=20,
        greedy_anchor_cells=4,
        stochastic_task_runs=160,
        greedy_anchor_task_runs=32,
        total_task_runs=192,
    )


def plan_as_dict(plan: StudyPlan) -> dict[str, Any]:
    """Typed convenience for callers that need a JSON-ready plan."""

    return plan.model_dump(mode="json")
