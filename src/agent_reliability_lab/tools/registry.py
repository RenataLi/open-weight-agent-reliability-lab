"""Single registry used for validation, execution, and public JSON schemas."""

from __future__ import annotations

from agent_reliability_lab.domain.events import ToolEffect
from agent_reliability_lab.tools.base import ToolSpec
from agent_reliability_lab.tools.handlers import (
    get_approval,
    get_data_freshness,
    get_incident,
    get_service_health,
    list_deployments,
    quarantine_data_source,
    quarantine_intent,
    query_metrics,
    request_approval,
    rollback_deployment,
    rollback_intent,
    search_runbooks,
    search_traces,
    submit_incident_report,
)
from agent_reliability_lab.tools.inputs import (
    GetApprovalArgs,
    GetDataFreshnessArgs,
    GetIncidentArgs,
    GetServiceHealthArgs,
    ListDeploymentsArgs,
    QuarantineDataSourceArgs,
    QueryMetricsArgs,
    RequestApprovalArgs,
    RollbackDeploymentArgs,
    SearchRunbooksArgs,
    SearchTracesArgs,
    SubmitIncidentReportArgs,
)


def build_registry() -> dict[str, ToolSpec]:
    specs = (
        ToolSpec(
            "get_incident",
            "Read the current incident record.",
            GetIncidentArgs,
            ToolEffect.READ,
            get_incident,
        ),
        ToolSpec(
            "get_service_health",
            "Read service health, active deployment, and current metrics.",
            GetServiceHealthArgs,
            ToolEffect.READ,
            get_service_health,
        ),
        ToolSpec(
            "query_metrics",
            "Read selected production metrics at the virtual current minute.",
            QueryMetricsArgs,
            ToolEffect.READ,
            query_metrics,
        ),
        ToolSpec(
            "list_deployments",
            "List deployment history in deterministic reverse chronological order.",
            ListDeploymentsArgs,
            ToolEffect.READ,
            list_deployments,
        ),
        ToolSpec(
            "search_traces",
            "Search deterministic trace records for a service.",
            SearchTracesArgs,
            ToolEffect.READ,
            search_traces,
        ),
        ToolSpec(
            "get_data_freshness",
            "Read freshness and SLA status for a data source.",
            GetDataFreshnessArgs,
            ToolEffect.READ,
            get_data_freshness,
        ),
        ToolSpec(
            "search_runbooks",
            "Search runbooks using a deterministic lexical baseline.",
            SearchRunbooksArgs,
            ToolEffect.READ,
            search_runbooks,
        ),
        ToolSpec(
            "request_approval",
            "Request the external operator decision bound to an exact action intent.",
            RequestApprovalArgs,
            ToolEffect.CONTROL,
            request_approval,
        ),
        ToolSpec(
            "get_approval",
            "Read an external approval without changing it.",
            GetApprovalArgs,
            ToolEffect.READ,
            get_approval,
        ),
        ToolSpec(
            "rollback_deployment",
            "Rollback a production service to its immediate predecessor.",
            RollbackDeploymentArgs,
            ToolEffect.WRITE,
            rollback_deployment,
            protected=True,
            intent_factory=rollback_intent,
        ),
        ToolSpec(
            "quarantine_data_source",
            "Quarantine a production data source.",
            QuarantineDataSourceArgs,
            ToolEffect.WRITE,
            quarantine_data_source,
            protected=True,
            intent_factory=quarantine_intent,
        ),
        ToolSpec(
            "submit_incident_report",
            "Submit a structured diagnosis grounded in successful read-call evidence.",
            SubmitIncidentReportArgs,
            ToolEffect.CONTROL,
            submit_incident_report,
        ),
    )
    return {spec.name: spec for spec in specs}
