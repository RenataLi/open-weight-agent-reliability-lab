"""Typed chat-completion contracts shared by model providers."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from agent_reliability_lab.domain.base import StrictModel

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class ChatRole(StrEnum):
    """Roles supported by the OpenAI chat-completions subset."""

    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FunctionDefinition(StrictModel):
    """JSON-schema definition exposed to a tool-calling model."""

    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, JsonValue]


class ChatTool(StrictModel):
    """OpenAI-compatible function tool declaration."""

    type: Literal["function"] = "function"
    function: FunctionDefinition


class ToolCallFunction(StrictModel):
    """Function selected by the model; arguments remain encoded JSON."""

    name: str = Field(min_length=1)
    arguments: str


class ToolCall(StrictModel):
    """A single assistant function call."""

    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(StrictModel):
    """Text and tool-call fields needed for a benchmark conversation."""

    role: ChatRole
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def validate_role_fields(self) -> ChatMessage:
        if self.role is ChatRole.TOOL and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.tool_calls and self.role is not ChatRole.ASSISTANT:
            raise ValueError("tool_calls are only valid on assistant messages")
        if self.role is not ChatRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid on tool messages")
        return self


class TokenUsage(StrictModel):
    """Token accounting returned by the inference server."""

    prompt_tokens: NonNegativeInt
    completion_tokens: NonNegativeInt
    total_tokens: NonNegativeInt

    @model_validator(mode="after")
    def validate_total(self) -> TokenUsage:
        if self.total_tokens < self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens cannot be smaller than prompt plus completion tokens")
        return self


class ChatCompletion(StrictModel):
    """Provider-neutral result for the first completion choice."""

    id: str
    model: str
    message: ChatMessage
    finish_reason: str | None
    usage: TokenUsage | None = None
    latency_ms: NonNegativeFloat
    provider_attempts: PositiveInt = 1
    retry_error_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_attempt_metadata(self) -> ChatCompletion:
        if len(self.retry_error_codes) >= self.provider_attempts:
            raise ValueError("retry_error_codes must describe attempts before the successful one")
        return self
