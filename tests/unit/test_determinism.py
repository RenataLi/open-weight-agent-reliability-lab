from __future__ import annotations

from collections.abc import Callable

import pytest

from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.simulator.determinism import canonical_json, stable_digest, stable_id
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import BenchmarkTask


def test_canonical_hash_ignores_mapping_insertion_order() -> None:
    left = {"outer": {"b": 2, "a": 1}, "items": [3, 2, 1]}
    right = {"items": [3, 2, 1], "outer": {"a": 1, "b": 2}}
    assert canonical_json(left) == canonical_json(right)
    assert stable_digest(left) == stable_digest(right)


def test_canonical_hash_preserves_list_order() -> None:
    assert stable_digest([1, 2]) != stable_digest([2, 1])


def test_canonicalization_handles_enums_and_sets_deterministically() -> None:
    assert canonical_json({ToolStatus.OK, ToolStatus.ERROR}) == '["error","ok"]'


def test_canonicalization_rejects_unsupported_objects() -> None:
    with pytest.raises(TypeError, match="Unsupported canonical value"):
        canonical_json(object())
    with pytest.raises(ValueError, match="sequence must be positive"):
        stable_id("call", 0)


def test_read_only_call_does_not_change_state(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S01_BAD_DEPLOY_DIAGNOSIS"))
    before = simulator.state_hash
    result = simulator.call_tool("get_service_health", {"service_id": "ranker-prod"})
    assert result.status.value == "ok"
    assert simulator.state_hash == before
    assert simulator.world.revision == 0


def test_reset_restores_world_session_and_fault_counters(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S11_RETRY_AFTER_HTTP_500")
    simulator = simulator_for(task)
    first = simulator.call_tool("query_metrics", {"service_id": "ranker-prod"})
    assert first.error is not None and first.error.code == "tool_upstream_error"
    assert simulator.attempt_counts == {"query_metrics": 1}

    simulator.reset()

    assert simulator.session.trace == []
    assert simulator.attempt_counts == {}
    assert simulator.world.revision == 0
    repeated = simulator.call_tool("query_metrics", {"service_id": "ranker-prod"})
    assert repeated.error is not None and repeated.error.code == "tool_upstream_error"


def test_simulator_instances_do_not_share_mutable_state(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S06_APPROVED_ROLLBACK")
    first = simulator_for(task)
    second = simulator_for(task)
    step = task.reference_trajectory[1]
    result = first.call_tool(step.tool_name, step.arguments)
    assert result.committed
    assert first.world.services["ranker-prod"].active_deployment_id == "deploy-001"
    assert second.world.services["ranker-prod"].active_deployment_id == "deploy-002"
