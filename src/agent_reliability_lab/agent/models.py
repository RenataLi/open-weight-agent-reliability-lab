"""Strict contracts for model-driven benchmark execution."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from agent_reliability_lab.domain.base import StrictModel
from agent_reliability_lab.domain.events import ToolResult, ToolTrace
from agent_reliability_lab.domain.models import (
    FindingFlag,
    RecommendedAction,
    RootCause,
    VerificationStatus,
)
from agent_reliability_lab.tasks.models import EvaluationCheck

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class AgentPolicy(StrEnum):
    """Prompt-level reliability policy used for controlled comparisons."""

    BASELINE = "baseline"
    RETRY_AWARE = "retry_aware"
    IDEMPOTENT = "idempotent"
    POLICY_AWARE = "policy_aware"


class TerminationReason(StrEnum):
    REPORT_SUBMITTED = "report_submitted"
    TASK_COMPLETE = "task_complete"
    NO_TOOL_CALL = "no_tool_call"
    TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
    MAX_TURNS = "max_turns"
    PROVIDER_ERROR = "provider_error"
    INVALID_MODEL_OUTPUT = "invalid_model_output"


class AgentReportExpectation(StrictModel):
    incident_id: str
    cause: RootCause
    culprit_id: str | None = None
    recommended_action: RecommendedAction
    verification: VerificationStatus
    finding_flags: frozenset[FindingFlag] = frozenset()


class AgentCallExpectation(StrictModel):
    """Order-independent proof that a particular grounded call occurred."""

    tool_name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    result: dict[str, JsonValue] = Field(default_factory=dict)
    result_contains: tuple[str, ...] = ()
    must_be_cited: bool = False


class AgentRetryExpectation(StrictModel):
    """Proof that the exact failed read was retried successfully."""

    tool_name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str
    success_must_be_cited: bool = False


class AgentTaskPrompt(StrictModel):
    prompt: str
    max_tool_calls: PositiveInt | None = None
    require_report: bool = True
    required_tools: frozenset[str] = frozenset()
    required_evidence_tools: frozenset[str] = frozenset()
    required_calls: tuple[AgentCallExpectation, ...] = ()
    required_retries: tuple[AgentRetryExpectation, ...] = ()
    required_error_codes: frozenset[str] = frozenset()
    forbidden_tools: frozenset[str] = frozenset()
    expected_report: AgentReportExpectation | None = None
    max_unsafe_action_attempts: NonNegativeInt = 0


class AgentPromptSet(StrictModel):
    schema_version: Literal["1.0"]
    prompt_set_id: str
    suite_id: str
    description: str
    tasks: dict[str, AgentTaskPrompt]

    @model_validator(mode="after")
    def validate_prompts(self) -> AgentPromptSet:
        if not self.tasks:
            raise ValueError("an agent prompt set must contain at least one task")
        if any(not task.prompt.strip() for task in self.tasks.values()):
            raise ValueError("agent prompts must not be empty")
        return self


class AgentRunConfig(StrictModel):
    policy: AgentPolicy = AgentPolicy.POLICY_AWARE
    seed: NonNegativeInt = 42
    temperature: Annotated[float, Field(strict=True, ge=0, le=2, allow_inf_nan=False)] = 0.0
    top_p: Annotated[float, Field(strict=True, gt=0, le=1, allow_inf_nan=False)] = 1.0
    max_tokens: PositiveInt = 2048
    max_turns: PositiveInt = 12


class ModelTurnRecord(StrictModel):
    turn: PositiveInt
    latency_ms: NonNegativeFloat
    finish_reason: str | None = None
    prompt_tokens: NonNegativeInt = 0
    completion_tokens: NonNegativeInt = 0
    assistant_content: str | None = None
    tool_call_count: NonNegativeInt = 0
    provider_attempts: PositiveInt = 1
    provider_retry_error_codes: tuple[str, ...] = ()
    provider_attempt_error_codes: tuple[str, ...] = ()
    provider_error_code: str | None = None


class ExecutedToolCall(StrictModel):
    turn: PositiveInt
    provider_call_id: str
    tool_name: str
    raw_arguments: str
    arguments: dict[str, JsonValue] | None = None
    result: ToolResult | None = None
    parse_error: str | None = None


class AgentTaskResult(StrictModel):
    task_id: str
    repetition: PositiveInt
    passed: bool
    termination_reason: TerminationReason
    checks: tuple[EvaluationCheck, ...]
    model_turns: tuple[ModelTurnRecord, ...]
    tool_calls: tuple[ExecutedToolCall, ...]
    simulator_trace: tuple[ToolTrace, ...]
    final_state_hash: str
    trace_hash: str
    world_revision: NonNegativeInt
    total_latency_ms: NonNegativeFloat
    prompt_tokens: NonNegativeInt
    completion_tokens: NonNegativeInt
    unsafe_action_attempts: NonNegativeInt
    committed_effects: NonNegativeInt
    replayed_calls: NonNegativeInt
    provider_attempts: NonNegativeInt
    provider_retries: NonNegativeInt
    provider_retry_error_codes: tuple[str, ...] = ()
    provider_attempt_error_codes: tuple[str, ...] = ()
    infrastructure_error: str | None = None


class AgentSuiteResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite_id: str
    prompt_set_id: str
    model: str
    model_revision: str | None = None
    provider_base_url: str
    policy: AgentPolicy
    repetitions: PositiveInt
    passed: bool
    passed_runs: NonNegativeInt
    total_runs: NonNegativeInt
    expected_runs: NonNegativeInt
    aborted: bool = False
    abort_reason: str | None = None
    task_results: tuple[AgentTaskResult, ...]
    aggregate: dict[str, JsonValue]
