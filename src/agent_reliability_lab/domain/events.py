"""Stable result and trace contracts at the simulator boundary."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, JsonValue

from agent_reliability_lab.domain.base import StrictModel


class ToolEffect(StrEnum):
    READ = "read"
    CONTROL = "control"
    WRITE = "write"


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    APPROVAL_REQUIRED = "approval_required"
    POLICY_DENIED = "policy_denied"
    CONFLICT = "conflict"


class PolicyDecision(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class FaultPhase(StrEnum):
    BEFORE_EFFECT = "before_effect"
    AFTER_EFFECT = "after_effect"


class FaultKind(StrEnum):
    TIMEOUT = "timeout"
    HTTP_429 = "http_429"
    HTTP_500 = "http_500"
    MALFORMED_RESPONSE = "malformed_response"
    STALE_RESPONSE = "stale_response"
    LOST_RESPONSE = "lost_response"


class ErrorDetail(StrictModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResult(StrictModel):
    call_id: str
    tool_name: str
    status: ToolStatus
    data: dict[str, JsonValue] | None = None
    error: ErrorDetail | None = None
    state_revision: int
    state_hash: str
    committed: bool = False
    replayed: bool = False
    stale: bool = False
    fault_id: str | None = None


class ToolTrace(StrictModel):
    sequence: int
    call_id: str
    tool_name: str
    effect: ToolEffect | None
    status: ToolStatus
    request_digest: str
    state_before_hash: str
    state_after_hash: str
    state_revision: int
    policy_decision: PolicyDecision
    error_code: str | None = None
    committed: bool = False
    replayed: bool = False
    stale: bool = False
    fault_id: str | None = None


class DomainEvent(StrictModel):
    event_id: str
    sequence: int
    minute: int
    event_type: str
    tool_name: str
    intent_digest: str
    principal_id: str
    policy_decision: PolicyDecision
    approval_id: str | None = None
    idempotency_scope: str
    state_revision: int
    payload: dict[str, JsonValue]
