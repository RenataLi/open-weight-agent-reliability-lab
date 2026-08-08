"""Strict input schemas exposed to an agent."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, JsonValue, model_validator

from agent_reliability_lab.domain.base import StrictModel
from agent_reliability_lab.domain.models import (
    FindingFlag,
    RecommendedAction,
    RootCause,
    VerificationStatus,
)

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
IdempotencyKey = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=100)]


class GetIncidentArgs(StrictModel):
    incident_id: Identifier


class GetServiceHealthArgs(StrictModel):
    service_id: Identifier


class QueryMetricsArgs(StrictModel):
    service_id: Identifier
    metric_names: tuple[str, ...] = (
        "p95_latency_ms",
        "error_rate",
        "quality_score",
        "throughput_rps",
    )

    @model_validator(mode="after")
    def validate_metrics(self) -> QueryMetricsArgs:
        allowed = {
            "p95_latency_ms",
            "error_rate",
            "quality_score",
            "throughput_rps",
        }
        if not self.metric_names:
            raise ValueError("metric_names must not be empty")
        unknown = set(self.metric_names) - allowed
        if unknown:
            raise ValueError(f"unknown metrics: {sorted(unknown)}")
        if len(self.metric_names) != len(set(self.metric_names)):
            raise ValueError("metric_names must be unique")
        return self


class ListDeploymentsArgs(StrictModel):
    service_id: Identifier
    limit: PositiveInt = 10


class SearchTracesArgs(StrictModel):
    service_id: Identifier
    contains: str = ""
    limit: PositiveInt = 10


class GetDataFreshnessArgs(StrictModel):
    data_source_id: Identifier


class SearchRunbooksArgs(StrictModel):
    query: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    limit: PositiveInt = 5


class RequestApprovalArgs(StrictModel):
    tool_name: Identifier
    resource_id: Identifier
    arguments: dict[str, JsonValue]
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class GetApprovalArgs(StrictModel):
    approval_id: Identifier


class RollbackDeploymentArgs(StrictModel):
    service_id: Identifier
    target_deployment_id: Identifier
    expected_revision: NonNegativeInt
    idempotency_key: IdempotencyKey
    approval_id: Identifier | None = None


class QuarantineDataSourceArgs(StrictModel):
    data_source_id: Identifier
    expected_revision: NonNegativeInt
    idempotency_key: IdempotencyKey
    approval_id: Identifier | None = None


class SubmitIncidentReportArgs(StrictModel):
    incident_id: Identifier
    cause: RootCause
    culprit_id: Identifier | None = None
    recommended_action: RecommendedAction
    verification: VerificationStatus
    evidence_call_ids: tuple[Identifier, ...]
    finding_flags: frozenset[FindingFlag] = frozenset()

    @model_validator(mode="after")
    def validate_evidence(self) -> SubmitIncidentReportArgs:
        if not self.evidence_call_ids:
            raise ValueError("at least one evidence call is required")
        if len(self.evidence_call_ids) != len(set(self.evidence_call_ids)):
            raise ValueError("evidence_call_ids must be unique")
        return self
