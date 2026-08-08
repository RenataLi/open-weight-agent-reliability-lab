from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agent_reliability_lab.domain.models import ExecutionLimits, ScenarioDefinition
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.simulator.state import InvariantViolation
from agent_reliability_lab.tasks.models import BenchmarkSuite, BenchmarkTask


def test_unknown_benchmark_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        BenchmarkSuite.model_validate(
            {
                "schema_version": "1.0",
                "suite_id": "bad",
                "description": "bad",
                "fixtures": {},
                "tasks": [],
                "surprise": True,
            }
        )


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BenchmarkSuite.model_validate(
            {
                "schema_version": "2.0",
                "suite_id": "bad",
                "description": "bad",
                "fixtures": {},
                "tasks": [],
            }
        )


def test_bool_is_not_accepted_as_strict_integer(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S01_BAD_DEPLOY_DIAGNOSIS"))
    result = simulator.call_tool("list_deployments", {"service_id": "ranker-prod", "limit": True})
    assert result.error is not None and result.error.code == "invalid_arguments"


def test_invalid_world_invariant_is_rejected(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S01_BAD_DEPLOY_DIAGNOSIS")
    simulator = simulator_for(task)
    broken = simulator._scenario.model_copy(deep=True)
    broken.world.services["ranker-prod"].active_deployment_id = "missing"
    with pytest.raises(InvariantViolation):
        Simulator(
            broken,
            principal_id="test-agent",
            limits=ExecutionLimits(
                allowed_tools=task.allowed_tools,
                max_tool_calls=task.max_tool_calls,
            ),
        )


def test_suite_round_trip_preserves_semantics(
    suite: BenchmarkSuite,
) -> None:
    payload = suite.model_dump(mode="json")
    loaded = BenchmarkSuite.model_validate_json(json.dumps(payload))
    assert loaded == suite


def test_invalid_lost_response_phase_is_rejected(
    suite: BenchmarkSuite,
) -> None:
    raw = suite.fixtures["bad-deploy"].model_dump(mode="json")
    raw["faults"] = [
        {
            "fault_id": "invalid",
            "tool_name": "rollback_deployment",
            "attempt": 1,
            "phase": "before_effect",
            "kind": "lost_response",
        }
    ]
    with pytest.raises(ValidationError, match="lost_response"):
        ScenarioDefinition.model_validate(raw)
