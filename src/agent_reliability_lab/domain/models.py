"""Domain state for a deterministic MLOps incident simulation."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from agent_reliability_lab.domain.base import StrictModel
from agent_reliability_lab.domain.events import (
    DomainEvent,
    FaultKind,
    FaultPhase,
    ToolResult,
    ToolTrace,
)
from agent_reliability_lab.domain.intents import binding_digest

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class DeploymentStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class DataSourceStatus(StrEnum):
    ACTIVE = "active"
    STALE = "stale"
    QUARANTINED = "quarantined"


class IncidentStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CONSUMED = "consumed"


class RootCause(StrEnum):
    BAD_DEPLOYMENT = "bad_deployment"
    STALE_DATA = "stale_data"
    FALSE_POSITIVE = "false_positive"
    UNKNOWN = "unknown"


class RecommendedAction(StrEnum):
    ROLLBACK = "rollback"
    QUARANTINE = "quarantine"
    OBSERVE = "observe"
    ESCALATE = "escalate"


class VerificationStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    CONTAINED = "contained"
    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"


class FindingFlag(StrEnum):
    PROMPT_INJECTION_DETECTED = "prompt_injection_detected"


class MetricSnapshot(StrictModel):
    p95_latency_ms: NonNegativeFloat
    error_rate: Probability
    quality_score: Probability
    throughput_rps: NonNegativeFloat


class DeploymentState(StrictModel):
    deployment_id: str
    service_id: str
    model_id: str
    status: DeploymentStatus
    deployed_minute: NonNegativeInt
    previous_deployment_id: str | None = None
    steady_state_metrics: MetricSnapshot


class ServiceState(StrictModel):
    service_id: str
    environment: str = "production"
    active_deployment_id: str
    health: HealthStatus
    observed_metrics: MetricSnapshot


class DataSourceState(StrictModel):
    data_source_id: str
    status: DataSourceStatus
    last_success_minute: NonNegativeInt
    freshness_sla_minutes: PositiveInt


class IncidentState(StrictModel):
    incident_id: str
    service_id: str
    status: IncidentStatus
    alert: str
    opened_minute: NonNegativeInt


class TraceRecord(StrictModel):
    trace_id: str
    service_id: str
    deployment_id: str
    minute: NonNegativeInt
    severity: str
    message: str


class Runbook(StrictModel):
    runbook_id: str
    title: str
    content: str
    tags: tuple[str, ...] = ()


class ApprovalState(StrictModel):
    approval_id: str
    tool_name: str
    resource_id: str
    authorized_arguments: dict[str, JsonValue]
    status: ApprovalStatus
    expires_at_minute: NonNegativeInt
    approved_by: str | None = None
    consumed_event_id: str | None = None

    @property
    def intent_digest(self) -> str:
        return binding_digest(self.tool_name, self.resource_id, self.authorized_arguments)


class IdempotencyRecord(StrictModel):
    scope: str
    tool_name: str
    request_digest: str
    intent_digest: str
    event_id: str
    result_data: dict[str, JsonValue]
    committed_revision: NonNegativeInt


class WorldState(StrictModel):
    scenario_id: str
    revision: NonNegativeInt = 0
    now_minute: NonNegativeInt
    next_event_sequence: PositiveInt = 1
    services: dict[str, ServiceState]
    deployments: dict[str, DeploymentState]
    data_sources: dict[str, DataSourceState] = Field(default_factory=dict)
    incidents: dict[str, IncidentState]
    traces: tuple[TraceRecord, ...] = ()
    runbooks: dict[str, Runbook] = Field(default_factory=dict)
    approvals: dict[str, ApprovalState] = Field(default_factory=dict)
    idempotency_ledger: dict[str, IdempotencyRecord] = Field(default_factory=dict)
    events: tuple[DomainEvent, ...] = ()


class FaultSpec(StrictModel):
    fault_id: str
    tool_name: str
    attempt: PositiveInt
    phase: FaultPhase
    kind: FaultKind
    payload: dict[str, JsonValue] | None = None


class ScenarioDefinition(StrictModel):
    scenario_id: str
    world: WorldState
    faults: tuple[FaultSpec, ...] = ()

    @model_validator(mode="after")
    def validate_identity_and_faults(self) -> ScenarioDefinition:
        if self.world.scenario_id != self.scenario_id:
            raise ValueError("world.scenario_id must match scenario_id")
        seen: set[tuple[str, int, FaultPhase]] = set()
        for fault in self.faults:
            key = (fault.tool_name, fault.attempt, fault.phase)
            if key in seen:
                raise ValueError(f"duplicate fault slot: {key}")
            seen.add(key)
            if fault.kind is FaultKind.LOST_RESPONSE and fault.phase is not FaultPhase.AFTER_EFFECT:
                raise ValueError("lost_response faults must run after_effect")
            if fault.kind is FaultKind.STALE_RESPONSE and fault.payload is None:
                raise ValueError("stale_response faults require payload")
        return self


class SessionState(StrictModel):
    principal_id: str
    call_sequence: NonNegativeInt = 0
    trace: list[ToolTrace] = Field(default_factory=list)
    observation_call_ids: set[str] = Field(default_factory=set)
    submitted_report: IncidentReport | None = None


class IncidentReport(StrictModel):
    incident_id: str
    cause: RootCause
    culprit_id: str | None = None
    recommended_action: RecommendedAction
    verification: VerificationStatus
    evidence_call_ids: tuple[str, ...]
    finding_flags: frozenset[FindingFlag] = frozenset()


class ExecutionLimits(StrictModel):
    allowed_tools: frozenset[str]
    forbidden_intents: frozenset[str] = frozenset()
    max_tool_calls: PositiveInt


class StoredResult(StrictModel):
    result: ToolResult
