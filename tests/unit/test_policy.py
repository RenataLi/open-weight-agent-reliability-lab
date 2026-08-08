from __future__ import annotations

from collections.abc import Callable

from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import BenchmarkTask


def _rollback(approval_id: str | None) -> dict[str, object]:
    arguments: dict[str, object] = {
        "service_id": "ranker-prod",
        "target_deployment_id": "deploy-001",
        "expected_revision": 0,
        "idempotency_key": "policy-test-001",
    }
    if approval_id is not None:
        arguments["approval_id"] = approval_id
    return arguments


def test_protected_action_requires_approval_without_consuming_fault_attempt(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S06_APPROVED_ROLLBACK"))
    before = simulator.state_hash
    result = simulator.call_tool("rollback_deployment", _rollback(None))  # type: ignore[arg-type]
    assert result.status is ToolStatus.APPROVAL_REQUIRED
    assert result.error is not None and result.error.code == "approval_required"
    assert simulator.state_hash == before
    assert simulator.world.revision == 0
    assert simulator.attempt_counts == {}
    assert simulator.world.idempotency_ledger == {}


def test_exact_approval_commits_and_is_consumed(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S06_APPROVED_ROLLBACK"))
    result = simulator.call_tool(
        "rollback_deployment",
        _rollback("approval-10-good"),  # type: ignore[arg-type]
    )
    assert result.status is ToolStatus.OK
    assert result.committed
    assert simulator.world.revision == 1
    assert simulator.world.approvals["approval-10-good"].status.value == "consumed"
    assert len(simulator.world.events) == 1


def test_mismatched_and_expired_approvals_fail_closed(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    for approval_id, expected_code in (
        ("approval-30-mismatch", "approval_mismatch"),
        ("approval-20-expired", "approval_expired"),
    ):
        simulator = simulator_for(task_by_id("S06_APPROVED_ROLLBACK"))
        result = simulator.call_tool(
            "rollback_deployment",
            _rollback(approval_id),  # type: ignore[arg-type]
        )
        assert result.status is ToolStatus.POLICY_DENIED
        assert result.error is not None and result.error.code == expected_code
        assert simulator.world.revision == 0
        assert simulator.attempt_counts == {}


def test_retrieved_prompt_injection_cannot_override_task_policy(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S04_RUNBOOK_INJECTION")
    simulator = simulator_for(task)
    search = simulator.call_tool(
        "search_runbooks", {"query": "emergency rollback override approval", "limit": 2}
    )
    assert search.status is ToolStatus.OK
    before = simulator.state_hash
    denied = simulator.call_tool(
        "rollback_deployment",
        {
            "service_id": "ranker-prod",
            "target_deployment_id": "deploy-001",
            "expected_revision": 0,
            "idempotency_key": "prompt-injection-test",
            "approval_id": "approval-10-good",
        },
    )
    assert denied.status is ToolStatus.POLICY_DENIED
    assert denied.error is not None and denied.error.code == "intent_forbidden"
    assert simulator.state_hash == before


def test_pending_missing_and_consumed_approvals_have_distinct_reason_codes(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    task = task_by_id("S06_APPROVED_ROLLBACK")

    pending = simulator_for(task).call_tool(
        "rollback_deployment",
        _rollback("approval-00-pending"),  # type: ignore[arg-type]
    )
    assert pending.status is ToolStatus.APPROVAL_REQUIRED
    assert pending.error is not None and pending.error.code == "approval_pending"

    missing = simulator_for(task).call_tool(
        "rollback_deployment",
        _rollback("approval-does-not-exist"),  # type: ignore[arg-type]
    )
    assert missing.status is ToolStatus.POLICY_DENIED
    assert missing.error is not None and missing.error.code == "approval_not_found"

    consumed_simulator = simulator_for(task)
    assert consumed_simulator.call_tool(
        "rollback_deployment",
        _rollback("approval-10-good"),  # type: ignore[arg-type]
    ).committed
    consumed = consumed_simulator.call_tool(
        "rollback_deployment",
        {
            "service_id": "ranker-prod",
            "target_deployment_id": "deploy-000",
            "expected_revision": 1,
            "idempotency_key": "policy-test-consumed",
            "approval_id": "approval-10-good",
        },
    )
    assert consumed.status is ToolStatus.POLICY_DENIED
    assert consumed.error is not None and consumed.error.code == "approval_consumed"


def test_request_approval_without_operator_decision_returns_unavailable(
    task_by_id: Callable[[str], BenchmarkTask],
    simulator_for: Callable[[BenchmarkTask], Simulator],
) -> None:
    simulator = simulator_for(task_by_id("S05_APPROVAL_PENDING"))
    result = simulator.call_tool(
        "request_approval",
        {
            "tool_name": "rollback_deployment",
            "resource_id": "another-service",
            "arguments": {
                "service_id": "another-service",
                "from_deployment_id": "x",
                "target_deployment_id": "y",
            },
            "reason": "contract test",
        },
    )
    assert result.status is ToolStatus.OK
    assert result.data is not None and result.data["status"] == "unavailable"
