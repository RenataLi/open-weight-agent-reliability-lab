"""Pure reads and atomic transition handlers."""

from __future__ import annotations

import re
from typing import cast

from pydantic import BaseModel, JsonValue

from agent_reliability_lab.domain.intents import ActionIntent, binding_digest
from agent_reliability_lab.domain.models import (
    DataSourceStatus,
    DeploymentStatus,
    HealthStatus,
    IncidentReport,
    ServiceState,
    SessionState,
    WorldState,
)
from agent_reliability_lab.tools.base import ToolData, ToolExecutionError
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


def _service(world: WorldState, service_id: str) -> ServiceState:
    service = world.services.get(service_id)
    if service is None:
        raise ToolExecutionError("service_not_found", f"Unknown service: {service_id}")
    return service


def get_incident(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(GetIncidentArgs, raw)
    incident = world.incidents.get(args.incident_id)
    if incident is None:
        raise ToolExecutionError("incident_not_found", f"Unknown incident: {args.incident_id}")
    return cast(ToolData, incident.model_dump(mode="json"))


def get_service_health(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(GetServiceHealthArgs, raw)
    service = _service(world, args.service_id)
    return {
        "service_id": service.service_id,
        "active_deployment_id": service.active_deployment_id,
        "health": service.health.value,
        "metrics": cast(dict[str, JsonValue], service.observed_metrics.model_dump(mode="json")),
    }


def query_metrics(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(QueryMetricsArgs, raw)
    service = _service(world, args.service_id)
    metrics = service.observed_metrics.model_dump(mode="json")
    return {
        "service_id": args.service_id,
        "minute": world.now_minute,
        "metrics": {name: metrics[name] for name in args.metric_names},
    }


def list_deployments(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(ListDeploymentsArgs, raw)
    _service(world, args.service_id)
    deployments = sorted(
        (
            deployment
            for deployment in world.deployments.values()
            if deployment.service_id == args.service_id
        ),
        key=lambda item: (-item.deployed_minute, item.deployment_id),
    )[: args.limit]
    return {
        "service_id": args.service_id,
        "deployments": [item.model_dump(mode="json") for item in deployments],
    }


def search_traces(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(SearchTracesArgs, raw)
    _service(world, args.service_id)
    needle = args.contains.casefold().strip()
    matches = [
        trace
        for trace in world.traces
        if trace.service_id == args.service_id
        and (not needle or needle in trace.message.casefold())
    ]
    matches.sort(key=lambda item: (-item.minute, item.trace_id))
    return {
        "service_id": args.service_id,
        "traces": [item.model_dump(mode="json") for item in matches[: args.limit]],
    }


def get_data_freshness(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(GetDataFreshnessArgs, raw)
    source = world.data_sources.get(args.data_source_id)
    if source is None:
        raise ToolExecutionError(
            "data_source_not_found", f"Unknown data source: {args.data_source_id}"
        )
    age = world.now_minute - source.last_success_minute
    return {
        "data_source_id": source.data_source_id,
        "status": source.status.value,
        "age_minutes": age,
        "freshness_sla_minutes": source.freshness_sla_minutes,
        "within_sla": age <= source.freshness_sla_minutes,
    }


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w-]+", text.casefold()))


def search_runbooks(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(SearchRunbooksArgs, raw)
    query_tokens = _tokens(args.query)
    ranked: list[tuple[int, str, ToolData]] = []
    for runbook in world.runbooks.values():
        searchable = " ".join((runbook.title, runbook.content, *runbook.tags))
        score = len(query_tokens & _tokens(searchable))
        if score:
            ranked.append(
                (
                    -score,
                    runbook.runbook_id,
                    {
                        "runbook_id": runbook.runbook_id,
                        "title": runbook.title,
                        "content": runbook.content,
                        "tags": list(runbook.tags),
                        "score": score,
                    },
                )
            )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return {"query": args.query, "results": [item[2] for item in ranked[: args.limit]]}


def request_approval(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(RequestApprovalArgs, raw)
    requested_digest = binding_digest(args.tool_name, args.resource_id, args.arguments)
    matches = sorted(
        (
            approval
            for approval in world.approvals.values()
            if approval.intent_digest == requested_digest
        ),
        key=lambda item: item.approval_id,
    )
    if not matches:
        return {
            "status": "unavailable",
            "message": "No external operator decision is configured for this exact intent.",
        }
    approval = matches[0]
    return {
        "approval_id": approval.approval_id,
        "status": approval.status.value,
        "expires_at_minute": approval.expires_at_minute,
        "binding_digest": approval.intent_digest,
    }


def get_approval(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(GetApprovalArgs, raw)
    approval = world.approvals.get(args.approval_id)
    if approval is None:
        raise ToolExecutionError("approval_not_found", f"Unknown approval: {args.approval_id}")
    return {
        "approval_id": approval.approval_id,
        "tool_name": approval.tool_name,
        "resource_id": approval.resource_id,
        "authorized_arguments": approval.authorized_arguments,
        "status": approval.status.value,
        "expires_at_minute": approval.expires_at_minute,
        "binding_digest": approval.intent_digest,
    }


def rollback_intent(world: WorldState, raw: BaseModel) -> ActionIntent:
    args = cast(RollbackDeploymentArgs, raw)
    service = _service(world, args.service_id)
    return ActionIntent(
        action="rollback_deployment",
        resource_id=args.service_id,
        arguments={
            "service_id": args.service_id,
            "from_deployment_id": service.active_deployment_id,
            "target_deployment_id": args.target_deployment_id,
        },
    )


def rollback_deployment(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(RollbackDeploymentArgs, raw)
    service = _service(world, args.service_id)
    current = world.deployments[service.active_deployment_id]
    target = world.deployments.get(args.target_deployment_id)
    if target is None or target.service_id != args.service_id:
        raise ToolExecutionError(
            "rollback_target_not_found", "The target deployment does not belong to the service."
        )
    if current.previous_deployment_id != target.deployment_id:
        raise ToolExecutionError(
            "invalid_rollback_target", "Rollback is limited to the immediate predecessor."
        )
    if target.status is not DeploymentStatus.SUPERSEDED:
        raise ToolExecutionError(
            "invalid_deployment_state", "The rollback target must be superseded."
        )
    current.status = DeploymentStatus.ROLLED_BACK
    target.status = DeploymentStatus.ACTIVE
    service.active_deployment_id = target.deployment_id
    service.observed_metrics = target.steady_state_metrics.model_copy(deep=True)
    metrics = target.steady_state_metrics
    service.health = (
        HealthStatus.HEALTHY
        if metrics.error_rate < 0.05
        and metrics.p95_latency_ms < 500.0
        and metrics.quality_score >= 0.8
        else HealthStatus.DEGRADED
    )
    return {
        "service_id": service.service_id,
        "rolled_back_deployment_id": current.deployment_id,
        "active_deployment_id": target.deployment_id,
        "health": service.health.value,
    }


def quarantine_intent(world: WorldState, raw: BaseModel) -> ActionIntent:
    args = cast(QuarantineDataSourceArgs, raw)
    source = world.data_sources.get(args.data_source_id)
    if source is None:
        raise ToolExecutionError(
            "data_source_not_found", f"Unknown data source: {args.data_source_id}"
        )
    return ActionIntent(
        action="quarantine_data_source",
        resource_id=args.data_source_id,
        arguments={
            "data_source_id": args.data_source_id,
            "from_status": source.status.value,
        },
    )


def quarantine_data_source(world: WorldState, raw: BaseModel, _session: SessionState) -> ToolData:
    args = cast(QuarantineDataSourceArgs, raw)
    source = world.data_sources.get(args.data_source_id)
    if source is None:
        raise ToolExecutionError(
            "data_source_not_found", f"Unknown data source: {args.data_source_id}"
        )
    if source.status is DataSourceStatus.QUARANTINED:
        raise ToolExecutionError("already_quarantined", "The data source is already quarantined.")
    previous_status = source.status
    source.status = DataSourceStatus.QUARANTINED
    return {
        "data_source_id": source.data_source_id,
        "previous_status": previous_status.value,
        "status": source.status.value,
    }


def submit_incident_report(world: WorldState, raw: BaseModel, session: SessionState) -> ToolData:
    args = cast(SubmitIncidentReportArgs, raw)
    if args.incident_id not in world.incidents:
        raise ToolExecutionError("incident_not_found", f"Unknown incident: {args.incident_id}")
    missing = set(args.evidence_call_ids) - session.observation_call_ids
    if missing:
        raise ToolExecutionError(
            "invalid_evidence",
            "Every evidence_call_id must reference a successful read call in this session.",
            details={"missing_call_ids": cast(JsonValue, sorted(missing))},
        )
    report = IncidentReport(
        incident_id=args.incident_id,
        cause=args.cause,
        culprit_id=args.culprit_id,
        recommended_action=args.recommended_action,
        verification=args.verification,
        evidence_call_ids=args.evidence_call_ids,
        finding_flags=args.finding_flags,
    )
    session.submitted_report = report
    return cast(ToolData, report.model_dump(mode="json"))
