"""Tool protocol shared by the engine and registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, JsonValue

from agent_reliability_lab.domain.events import ToolEffect, ToolStatus
from agent_reliability_lab.domain.intents import ActionIntent
from agent_reliability_lab.domain.models import SessionState, WorldState

type ToolData = dict[str, JsonValue]
type ToolHandler = Callable[[WorldState, BaseModel, SessionState], ToolData]
type IntentFactory = Callable[[WorldState, BaseModel], ActionIntent]


class ToolExecutionError(RuntimeError):
    """Expected tool failure converted into a typed boundary result."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: ToolStatus = ToolStatus.ERROR,
        retryable: bool = False,
        details: ToolData | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    effect: ToolEffect
    handler: ToolHandler
    protected: bool = False
    intent_factory: IntentFactory | None = None

    def json_schema(self) -> ToolData:
        return {
            "name": self.name,
            "description": self.description,
            "effect": self.effect.value,
            "protected": self.protected,
            "parameters": self.input_model.model_json_schema(),
        }
