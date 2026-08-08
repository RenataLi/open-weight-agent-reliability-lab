from __future__ import annotations

from pathlib import Path

import pytest

from agent_reliability_lab.agent.models import AgentCallExpectation
from agent_reliability_lab.agent.prompt_sets import load_prompt_set, validate_prompt_set
from agent_reliability_lab.tasks.models import BenchmarkSuite

ROOT = Path(__file__).parents[2]


def test_model_prompt_set_cross_validates_against_tool_contract(
    suite: BenchmarkSuite,
) -> None:
    prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")

    validate_prompt_set(prompt_set, suite)


def test_prompt_set_rejects_required_tool_not_available_to_task(
    suite: BenchmarkSuite,
) -> None:
    prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S03_FALSE_ALERT"
    prompt = prompt_set.tasks[task_id].model_copy(
        update={
            "required_tools": prompt_set.tasks[task_id].required_tools | {"rollback_deployment"},
            "forbidden_tools": frozenset(),
        }
    )
    invalid = prompt_set.model_copy(update={"tasks": {task_id: prompt}})

    with pytest.raises(ValueError, match="requires unavailable tools"):
        validate_prompt_set(invalid, suite)


def test_prompt_set_rejects_control_tool_as_report_evidence(
    suite: BenchmarkSuite,
) -> None:
    prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S03_FALSE_ALERT"
    prompt = prompt_set.tasks[task_id].model_copy(
        update={
            "required_evidence_tools": prompt_set.tasks[task_id].required_evidence_tools
            | {"submit_incident_report"}
        }
    )
    invalid = prompt_set.model_copy(update={"tasks": {task_id: prompt}})

    with pytest.raises(ValueError, match="evidence tools must be read-only"):
        validate_prompt_set(invalid, suite)


def test_prompt_set_rejects_unknown_required_call_tool(
    suite: BenchmarkSuite,
) -> None:
    prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S03_FALSE_ALERT"
    prompt = prompt_set.tasks[task_id].model_copy(
        update={"required_calls": (AgentCallExpectation(tool_name="invented_tool"),)}
    )
    invalid = prompt_set.model_copy(update={"tasks": {task_id: prompt}})

    with pytest.raises(ValueError, match="references unknown tools"):
        validate_prompt_set(invalid, suite)
