"""Strict public benchmark and result schemas."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from agent_reliability_lab.domain.base import StrictModel
from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.domain.models import (
    ApprovalStatus,
    DataSourceStatus,
    FaultSpec,
    IncidentReport,
    ScenarioDefinition,
)

PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class ToolCallSpec(StrictModel):
    tool_name: str
    arguments: dict[str, JsonValue]


class ExpectedOutcome(StrictModel):
    status_sequence: tuple[ToolStatus, ...]
    error_codes: tuple[str | None, ...]
    active_deployments: dict[str, str]
    data_source_statuses: dict[str, DataSourceStatus] = Field(default_factory=dict)
    approval_statuses: dict[str, ApprovalStatus] = Field(default_factory=dict)
    world_revision_delta: NonNegativeInt
    policy_denials: NonNegativeInt = 0
    committed_effects: NonNegativeInt = 0
    replays: NonNegativeInt = 0
    attempt_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    report: IncidentReport | None = None

    @model_validator(mode="after")
    def validate_sequences(self) -> ExpectedOutcome:
        if len(self.status_sequence) != len(self.error_codes):
            raise ValueError("status_sequence and error_codes must have the same length")
        return self


class BenchmarkTask(StrictModel):
    task_id: str
    title: str
    prompt: str
    fixture_id: str
    principal_id: str = "benchmark-agent"
    allowed_tools: frozenset[str]
    forbidden_intents: frozenset[str] = frozenset()
    faults: tuple[FaultSpec, ...] = ()
    max_tool_calls: PositiveInt
    reference_trajectory: tuple[ToolCallSpec, ...]
    expected: ExpectedOutcome

    @model_validator(mode="after")
    def validate_budget(self) -> BenchmarkTask:
        if len(self.reference_trajectory) > self.max_tool_calls:
            raise ValueError("reference_trajectory exceeds max_tool_calls")
        return self


class BenchmarkSuite(StrictModel):
    schema_version: Literal["1.0"]
    suite_id: str
    description: str
    fixtures: dict[str, ScenarioDefinition]
    tasks: tuple[BenchmarkTask, ...]

    @model_validator(mode="after")
    def validate_references(self) -> BenchmarkSuite:
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        for fixture_id, fixture in self.fixtures.items():
            if fixture.scenario_id != fixture_id:
                raise ValueError(f"fixture key mismatch: {fixture_id}")
        for task in self.tasks:
            if task.fixture_id not in self.fixtures:
                raise ValueError(f"task {task.task_id} references an unknown fixture")
        return self


class EvaluationCheck(StrictModel):
    name: str
    passed: bool
    expected: JsonValue
    observed: JsonValue


class TaskRunResult(StrictModel):
    task_id: str
    passed: bool
    checks: tuple[EvaluationCheck, ...]
    statuses: tuple[ToolStatus, ...]
    error_codes: tuple[str | None, ...]
    final_state_hash: str
    trace_hash: str
    world_revision: NonNegativeInt


class SuiteRunResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    suite_id: str
    passed: bool
    passed_tasks: NonNegativeInt
    total_tasks: NonNegativeInt
    tasks: tuple[TaskRunResult, ...]
