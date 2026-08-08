"""Canonical action intents used by policy and idempotency checks."""

from __future__ import annotations

from pydantic import JsonValue

from agent_reliability_lab.domain.base import StrictModel
from agent_reliability_lab.simulator.determinism import stable_digest


class ActionIntent(StrictModel):
    action: str
    resource_id: str
    arguments: dict[str, JsonValue]

    @property
    def digest(self) -> str:
        return stable_digest(self)


def binding_digest(
    tool_name: str,
    resource_id: str,
    arguments: dict[str, JsonValue],
) -> str:
    return ActionIntent(
        action=tool_name,
        resource_id=resource_id,
        arguments=arguments,
    ).digest
