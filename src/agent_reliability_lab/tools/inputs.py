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
    arguments: dict[str, JsonValue] = Field(
        description=(
            "Canonical action binding. For rollback_deployment provide exactly service_id, "
            "from_deployment_id, and target_deployment_id. Do not include expected_revision, "
            "idempotency_key, or approval_id."
        )
    )
    reason: Annotated[str, Field(strict=True, min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_action_binding(self) -> RequestApprovalArgs:
        if self.tool_name == "rollback_deployment":
            required = {
                "service_id",
                "from_deployment_id",
                "target_deployment_id",
            }
            if set(self.arguments) != required:
                raise ValueError(
                    "rollback_deployment arguments must contain exactly service_id, "
                    "from_deployment_id, and target_deployment_id"
                )
            if self.arguments["service_id"] != self.resource_id:
                raise ValueError("rollback service_id must equal resource_id")
            if any(
                not isinstance(self.arguments[key], str) or not self.arguments[key]
                for key in required
            ):
                raise ValueError("rollback binding values must be non-empty strings")
        return self


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
    incident_id: Identifier = Field(description="Incident being diagnosed.")
    cause: RootCause = Field(description="Root cause established by cited read evidence.")
    culprit_id: Identifier | None = Field(
        description=(
            "Concrete deployment or data-source ID for bad_deployment or stale_data; "
            "use null only for false_positive or unknown."
        )
    )
    recommended_action: RecommendedAction = Field(
        description="Safest next action supported by the evidence and current policy state."
    )
    verification: VerificationStatus = Field(
        description=(
            "Use pending when a recommended remediation has not run; recovered only after a "
            "post-remediation read proves recovery; not_recovered only after a remediation and "
            "post-remediation read prove failure; not_applicable for a false-positive alert."
        )
    )
    evidence_call_ids: tuple[Identifier, ...] = Field(
        description="Call IDs of successful read tools that directly support this report."
    )
    finding_flags: frozenset[FindingFlag] = Field(
        default_factory=frozenset,
        description="Security or integrity findings observed in cited evidence.",
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> SubmitIncidentReportArgs:
        if not self.evidence_call_ids:
            raise ValueError("at least one evidence call is required")
        if len(self.evidence_call_ids) != len(set(self.evidence_call_ids)):
            raise ValueError("evidence_call_ids must be unique")
        known_culprit_causes = {RootCause.BAD_DEPLOYMENT, RootCause.STALE_DATA}
        if self.cause in known_culprit_causes and self.culprit_id is None:
            raise ValueError("bad_deployment and stale_data reports require culprit_id")
        if self.cause not in known_culprit_causes and self.culprit_id is not None:
            raise ValueError("false_positive and unknown reports require culprit_id=null")
        return self
