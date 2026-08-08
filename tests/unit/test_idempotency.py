from __future__ import annotations

from collections.abc import Callable

from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import BenchmarkTask


def test_successful_retry_is_replayed_without_second_commit(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S09_IDEMPOTENT_RETRY")
    simulator = simulator_for(task)
    step = task.reference_trajectory[0]
    first = simulator.call_tool(step.tool_name, step.arguments)
    second = simulator.call_tool(step.tool_name, step.arguments)
    assert first.committed and not first.replayed
    assert not second.committed and second.replayed
    assert second.status is ToolStatus.OK
    assert first.data == second.data
    assert simulator.world.revision == 1
    assert len(simulator.world.events) == 1
    assert simulator.attempt_counts == {"rollback_deployment": 1}


def test_same_key_with_different_payload_returns_conflict(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S10_IDEMPOTENCY_CONFLICT")
    simulator = simulator_for(task)
    first, second = task.reference_trajectory
    assert simulator.call_tool(first.tool_name, first.arguments).committed
    conflict = simulator.call_tool(second.tool_name, second.arguments)
    assert conflict.status is ToolStatus.CONFLICT
    assert conflict.error is not None and conflict.error.code == "idempotency_conflict"
    assert simulator.world.revision == 1
    assert len(simulator.world.events) == 1


def test_lost_response_retry_recovers_committed_result(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S12_LOST_RESPONSE_AFTER_COMMIT")
    simulator = simulator_for(task)
    rollback = task.reference_trajectory[1]
    lost = simulator.call_tool(rollback.tool_name, rollback.arguments)
    assert lost.status is ToolStatus.ERROR
    assert lost.committed
    assert lost.error is not None and lost.error.code == "response_lost_after_effect"
    retry = simulator.call_tool(rollback.tool_name, rollback.arguments)
    assert retry.status is ToolStatus.OK and retry.replayed
    assert simulator.world.revision == 1
    assert len(simulator.world.events) == 1


def test_reset_clears_idempotency_ledger(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S09_IDEMPOTENT_RETRY")
    simulator = simulator_for(task)
    step = task.reference_trajectory[0]
    assert simulator.call_tool(step.tool_name, step.arguments).committed
    assert simulator.world.idempotency_ledger
    simulator.reset()
    assert simulator.world.idempotency_ledger == {}
    assert simulator.call_tool(step.tool_name, step.arguments).committed
