from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agent_reliability_lab.domain.models import ExecutionLimits, ScenarioDefinition
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.simulator.state import InvariantViolation
from agent_reliability_lab.tasks.models import BenchmarkSuite, BenchmarkTask
from agent_reliability_lab.tools.inputs import (
    RequestApprovalArgs,
    RollbackDeploymentArgs,
    SubmitIncidentReportArgs,
)


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


def test_model_validator_error_context_is_json_serialized(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S01_BAD_DEPLOY_DIAGNOSIS"))

    result = simulator.call_tool(
        "submit_incident_report",
        {
            "incident_id": "inc-001",
            "cause": "bad_deployment",
            "culprit_id": None,
            "recommended_action": "rollback",
            "verification": "pending",
            "evidence_call_ids": ["call-0001"],
        },
    )

    assert result.error is not None and result.error.code == "invalid_arguments"
    validation_errors = result.error.details["validation_errors"]
    assert isinstance(validation_errors, list)
    validation_error = validation_errors[0]
    assert isinstance(validation_error, dict)
    assert validation_error["type"] == "value_error"
    context = validation_error["ctx"]
    assert isinstance(context, dict)
    assert context["error"] == ("bad_deployment and stale_data reports require culprit_id")
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


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


def test_incident_report_schema_requires_explicit_semantic_culprit() -> None:
    schema = SubmitIncidentReportArgs.model_json_schema()
    assert "culprit_id" in schema["required"]
    assert "pending" in schema["properties"]["verification"]["description"]
    assert "contained" in schema["properties"]["verification"]["description"]
    verification_reference = schema["properties"]["verification"]["$ref"]
    verification_name = verification_reference.rsplit("/", 1)[-1]
    assert "contained" in schema["$defs"][verification_name]["enum"]
    assert (
        "declarative and never executes"
        in schema["properties"]["recommended_action"]["description"]
    )

    base = {
        "incident_id": "inc-001",
        "cause": "bad_deployment",
        "recommended_action": "rollback",
        "verification": "pending",
        "evidence_call_ids": ["call-0001"],
    }
    with pytest.raises(ValidationError, match="culprit_id"):
        SubmitIncidentReportArgs.model_validate(base)
    with pytest.raises(ValidationError, match="require culprit_id"):
        SubmitIncidentReportArgs.model_validate({**base, "culprit_id": None})

    false_positive = SubmitIncidentReportArgs.model_validate(
        {
            **base,
            "cause": "false_positive",
            "culprit_id": None,
            "recommended_action": "observe",
            "verification": "not_applicable",
        }
    )
    assert false_positive.culprit_id is None


def test_write_schema_explains_world_revision_contract() -> None:
    schema = RollbackDeploymentArgs.model_json_schema()
    description = schema["properties"]["expected_revision"]["description"]
    assert "state_revision" in description
    assert "not a deployment number" in description


def test_quarantine_approval_binding_is_explicit_and_exact() -> None:
    schema = RequestApprovalArgs.model_json_schema()
    description = schema["properties"]["arguments"]["description"]
    assert "data_source_id and from_status" in description

    valid = RequestApprovalArgs.model_validate(
        {
            "tool_name": "quarantine_data_source",
            "resource_id": "catalog-features-v2",
            "arguments": {
                "data_source_id": "catalog-features-v2",
                "from_status": "stale",
            },
            "reason": "Freshness is outside the configured SLA.",
        }
    )
    assert valid.arguments["from_status"] == "stale"

    with pytest.raises(ValidationError, match="exactly data_source_id and from_status"):
        RequestApprovalArgs.model_validate(
            {
                "tool_name": "quarantine_data_source",
                "resource_id": "catalog-features-v2",
                "arguments": {"data_source_id": "catalog-features-v2"},
                "reason": "Freshness is outside the configured SLA.",
            }
        )
