from __future__ import annotations

from collections.abc import Callable

from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import BenchmarkTask


def test_before_effect_http_500_is_retryable_and_non_mutating(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S11_RETRY_AFTER_HTTP_500"))
    before = simulator.state_hash
    first = simulator.call_tool("query_metrics", {"service_id": "ranker-prod"})
    second = simulator.call_tool("query_metrics", {"service_id": "ranker-prod"})
    assert first.status is ToolStatus.ERROR
    assert first.error is not None and first.error.retryable
    assert second.status is ToolStatus.OK
    assert simulator.state_hash == before
    assert simulator.attempt_counts == {"query_metrics": 2}


def test_after_effect_fault_preserves_commit_and_ledger(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S12_LOST_RESPONSE_AFTER_COMMIT")
    simulator = simulator_for(task)
    step = task.reference_trajectory[1]
    result = simulator.call_tool(step.tool_name, step.arguments)
    assert result.status is ToolStatus.ERROR and result.committed
    assert simulator.world.services["ranker-prod"].active_deployment_id == "deploy-001"
    assert simulator.world.idempotency_ledger
    assert simulator.world.approvals["approval-10-good"].status.value == "consumed"


def test_policy_denial_does_not_consume_underlying_attempt(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S04_RUNBOOK_INJECTION"))
    result = simulator.call_tool(
        "rollback_deployment",
        {
            "service_id": "ranker-prod",
            "target_deployment_id": "deploy-001",
            "expected_revision": 0,
            "idempotency_key": "denied-before-attempt",
            "approval_id": "approval-10-good",
        },
    )
    assert result.status is ToolStatus.POLICY_DENIED
    assert simulator.attempt_counts == {}
