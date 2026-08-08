"""State hashing and invariant checks."""

from __future__ import annotations

from collections.abc import Iterable

from agent_reliability_lab.domain.events import PolicyDecision
from agent_reliability_lab.domain.models import (
    ApprovalStatus,
    DeploymentStatus,
    WorldState,
)
from agent_reliability_lab.simulator.determinism import stable_digest


class InvariantViolation(RuntimeError):
    """Raised before commit when a transition would create an invalid world."""


def business_state_hash(world: WorldState) -> str:
    """Hash production state while excluding trace and retry bookkeeping."""

    return stable_digest(
        {
            "scenario_id": world.scenario_id,
            "services": world.services,
            "deployments": world.deployments,
            "data_sources": world.data_sources,
            "incidents": world.incidents,
        }
    )


def full_state_hash(world: WorldState) -> str:
    return stable_digest(world)


def _assert_unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise InvariantViolation(f"duplicate {label}")


def validate_world(world: WorldState) -> None:
    """Validate cross-entity and safety invariants."""

    if world.revision != len(world.events):
        raise InvariantViolation("revision must equal the committed event count")
    if world.next_event_sequence != len(world.events) + 1:
        raise InvariantViolation("next_event_sequence is inconsistent")

    _assert_unique((event.event_id for event in world.events), "event id")
    expected_sequences = list(range(1, len(world.events) + 1))
    if [event.sequence for event in world.events] != expected_sequences:
        raise InvariantViolation("event sequences must be contiguous")
    if [event.state_revision for event in world.events] != expected_sequences:
        raise InvariantViolation("event revisions must be contiguous")

    for service_id, service in world.services.items():
        if service.service_id != service_id:
            raise InvariantViolation(f"service key mismatch: {service_id}")
        if service.active_deployment_id not in world.deployments:
            raise InvariantViolation(f"missing active deployment: {service.active_deployment_id}")
        active = [
            deployment
            for deployment in world.deployments.values()
            if deployment.service_id == service_id and deployment.status is DeploymentStatus.ACTIVE
        ]
        if len(active) != 1 or active[0].deployment_id != service.active_deployment_id:
            raise InvariantViolation(
                f"service {service_id} must have exactly one active deployment"
            )

    for deployment_id, deployment in world.deployments.items():
        if deployment.deployment_id != deployment_id:
            raise InvariantViolation(f"deployment key mismatch: {deployment_id}")
        if deployment.service_id not in world.services:
            raise InvariantViolation(f"deployment references unknown service: {deployment_id}")
        if (
            deployment.previous_deployment_id is not None
            and deployment.previous_deployment_id not in world.deployments
        ):
            raise InvariantViolation(f"deployment predecessor missing: {deployment_id}")

        seen: set[str] = set()
        cursor = deployment
        while cursor.previous_deployment_id is not None:
            if cursor.deployment_id in seen:
                raise InvariantViolation(f"deployment cycle detected at {deployment_id}")
            seen.add(cursor.deployment_id)
            cursor = world.deployments[cursor.previous_deployment_id]

    for incident_id, incident in world.incidents.items():
        if incident.incident_id != incident_id:
            raise InvariantViolation(f"incident key mismatch: {incident_id}")
        if incident.service_id not in world.services:
            raise InvariantViolation(f"incident references unknown service: {incident_id}")

    for trace in world.traces:
        if trace.service_id not in world.services:
            raise InvariantViolation(f"trace references unknown service: {trace.trace_id}")
        if trace.deployment_id not in world.deployments:
            raise InvariantViolation(f"trace references unknown deployment: {trace.trace_id}")

    event_ids = {event.event_id for event in world.events}
    for approval_id, approval in world.approvals.items():
        if approval.approval_id != approval_id:
            raise InvariantViolation(f"approval key mismatch: {approval_id}")
        if approval.status is ApprovalStatus.CONSUMED:
            if approval.consumed_event_id not in event_ids:
                raise InvariantViolation(f"consumed approval lacks committed event: {approval_id}")
        elif approval.consumed_event_id is not None:
            raise InvariantViolation(f"unconsumed approval references an event: {approval_id}")

    scopes: set[str] = set()
    for scope, record in world.idempotency_ledger.items():
        if record.scope != scope:
            raise InvariantViolation(f"idempotency scope mismatch: {scope}")
        if scope in scopes:
            raise InvariantViolation(f"duplicate idempotency scope: {scope}")
        scopes.add(scope)
        if record.event_id not in event_ids:
            raise InvariantViolation(f"idempotency record lacks event: {scope}")

    protected_events = {"rollback_deployment", "quarantine_data_source"}
    for event in world.events:
        if event.tool_name in protected_events and (
            event.policy_decision is not PolicyDecision.ALLOW or event.approval_id is None
        ):
            raise InvariantViolation(
                f"protected event lacks an allow decision and approval: {event.event_id}"
            )
