"""Approval binding and protected-action policy."""

from __future__ import annotations

from dataclasses import dataclass

from agent_reliability_lab.domain.events import PolicyDecision, ToolStatus
from agent_reliability_lab.domain.intents import ActionIntent
from agent_reliability_lab.domain.models import ApprovalState, ApprovalStatus, WorldState


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    decision: PolicyDecision
    status: ToolStatus
    code: str | None = None
    message: str | None = None
    approval: ApprovalState | None = None


def evaluate_protected_action(
    *,
    world: WorldState,
    intent: ActionIntent,
    approval_id: str | None,
    forbidden_intents: frozenset[str],
) -> PolicyOutcome:
    """Return a fail-closed policy decision without mutating state."""

    if intent.action in forbidden_intents:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="intent_forbidden",
            message=f"The task forbids {intent.action}.",
        )
    if not approval_id:
        return PolicyOutcome(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            status=ToolStatus.APPROVAL_REQUIRED,
            code="approval_required",
            message="A bound operator approval is required.",
        )
    approval = world.approvals.get(approval_id)
    if approval is None:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="approval_not_found",
            message="The approval does not exist in this scenario.",
        )
    if approval.status is ApprovalStatus.PENDING:
        return PolicyOutcome(
            decision=PolicyDecision.REQUIRE_APPROVAL,
            status=ToolStatus.APPROVAL_REQUIRED,
            code="approval_pending",
            message="The operator has not decided yet.",
            approval=approval,
        )
    if approval.status is ApprovalStatus.DENIED:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="approval_denied",
            message="The operator denied this action.",
            approval=approval,
        )
    if approval.status is ApprovalStatus.CONSUMED:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="approval_consumed",
            message="This single-use approval was already consumed.",
            approval=approval,
        )
    if world.now_minute >= approval.expires_at_minute:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="approval_expired",
            message="The approval is expired.",
            approval=approval,
        )
    if approval.tool_name != intent.action or approval.intent_digest != intent.digest:
        return PolicyOutcome(
            decision=PolicyDecision.DENY,
            status=ToolStatus.POLICY_DENIED,
            code="approval_mismatch",
            message="The approval is not bound to this exact action and resource snapshot.",
            approval=approval,
        )
    return PolicyOutcome(
        decision=PolicyDecision.ALLOW,
        status=ToolStatus.OK,
        approval=approval,
    )
