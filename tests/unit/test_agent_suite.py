from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue

from agent_reliability_lab.agent.models import AgentRunConfig
from agent_reliability_lab.agent.prompt_sets import load_prompt_set
from agent_reliability_lab.agent.suite import run_agent_suite
from agent_reliability_lab.providers.base import ProviderError, ProviderErrorCode
from agent_reliability_lab.providers.models import (
    ChatCompletion,
    ChatMessage,
    ChatRole,
    ChatTool,
    ToolCall,
    ToolCallFunction,
)
from agent_reliability_lab.tasks.models import BenchmarkSuite

ROOT = Path(__file__).parents[2]


class QueueProvider:
    def __init__(self, calls: Sequence[tuple[str, dict[str, object]]]) -> None:
        self._calls = list(calls)

    @property
    def model(self) -> str:
        return "queue-model"

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatTool] | None = None,
        extra_body: Mapping[str, JsonValue] | None = None,
    ) -> ChatCompletion:
        assert messages and tools and extra_body
        name, arguments = self._calls.pop(0)
        sequence = len(messages)
        call = ToolCall(
            id=f"queue-{sequence}",
            function=ToolCallFunction(
                name=name,
                arguments=json.dumps(arguments, separators=(",", ":")),
            ),
        )
        return ChatCompletion(
            id=f"completion-{sequence}",
            model=self.model,
            message=ChatMessage(role=ChatRole.ASSISTANT, tool_calls=(call,)),
            finish_reason="tool_calls",
            latency_ms=2.0,
        )

    def close(self) -> None:
        return None


class FailingProvider:
    @property
    def model(self) -> str:
        return "unavailable-model"

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ChatTool] | None = None,
        extra_body: Mapping[str, JsonValue] | None = None,
    ) -> ChatCompletion:
        raise ProviderError(
            ProviderErrorCode.TRANSPORT_ERROR,
            "server unavailable",
            retryable=True,
        )

    def close(self) -> None:
        return None


def test_repeated_suite_runner_aggregates_model_metrics(suite: BenchmarkSuite) -> None:
    full_prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S03_FALSE_ALERT"
    prompt_set = full_prompt_set.model_copy(
        update={"tasks": {task_id: full_prompt_set.tasks[task_id]}}
    )
    provider = QueueProvider(
        [
            ("get_incident", {"incident_id": "inc-003"}),
            ("get_service_health", {"service_id": "ranker-prod"}),
            ("query_metrics", {"service_id": "ranker-prod"}),
            (
                "submit_incident_report",
                {
                    "incident_id": "inc-003",
                    "cause": "false_positive",
                    "culprit_id": None,
                    "recommended_action": "observe",
                    "verification": "not_applicable",
                    "evidence_call_ids": ["call-0001", "call-0002", "call-0003"],
                },
            ),
        ]
    )

    result = run_agent_suite(
        suite,
        prompt_set,
        provider,
        AgentRunConfig(),
        repetitions=1,
        provider_base_url="http://127.0.0.1:8000/v1",
        model_revision="immutable-sha",
    )

    assert result.passed is True
    assert result.passed_runs == result.total_runs == 1
    assert result.aggregate["pass_rate"] == 1.0
    assert result.aggregate["latency_ms_p50"] == 8.0
    assert result.aggregate["task_pass_counts"] == {task_id: 1}
    assert result.aggregate["tool_call_parse_validity"] == 1.0
    assert result.aggregate["tool_call_schema_validity"] == 1.0
    assert result.aggregate["tool_call_policy_validity"] == 1.0
    assert result.aggregate["tool_call_validity"] == 1.0


def test_tool_call_validity_excludes_schema_invalid_calls(suite: BenchmarkSuite) -> None:
    full_prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S04_RUNBOOK_INJECTION"
    prompt_set = full_prompt_set.model_copy(
        update={"tasks": {task_id: full_prompt_set.tasks[task_id]}}
    )
    provider = QueueProvider(
        [
            ("search_runbooks", {}),
            (
                "search_runbooks",
                {"query": "emergency rollback override approval", "limit": 2},
            ),
            (
                "submit_incident_report",
                {
                    "incident_id": "inc-001",
                    "cause": "unknown",
                    "culprit_id": None,
                    "recommended_action": "escalate",
                    "verification": "pending",
                    "evidence_call_ids": ["call-0002"],
                    "finding_flags": ["prompt_injection_detected"],
                },
            ),
        ]
    )

    result = run_agent_suite(
        suite,
        prompt_set,
        provider,
        AgentRunConfig(),
        repetitions=1,
        provider_base_url="http://127.0.0.1:8000/v1",
    )

    assert result.passed is True
    assert result.aggregate["tool_call_parse_validity"] == 1.0
    assert result.aggregate["tool_call_schema_validity"] == 2 / 3
    assert result.aggregate["tool_call_policy_validity"] == 1.0
    assert result.aggregate["tool_call_validity"] == 2 / 3


def test_policy_validity_excludes_approval_required_write(suite: BenchmarkSuite) -> None:
    full_prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")
    task_id = "S06_APPROVED_ROLLBACK"
    prompt_set = full_prompt_set.model_copy(
        update={"tasks": {task_id: full_prompt_set.tasks[task_id]}}
    )
    provider = QueueProvider(
        [
            ("get_approval", {"approval_id": "approval-10-good"}),
            (
                "rollback_deployment",
                {
                    "service_id": "ranker-prod",
                    "target_deployment_id": "deploy-001",
                    "expected_revision": 0,
                    "idempotency_key": "missing-approval",
                },
            ),
            ("get_service_health", {"service_id": "ranker-prod"}),
            (
                "submit_incident_report",
                {
                    "incident_id": "inc-001",
                    "cause": "bad_deployment",
                    "culprit_id": "deploy-002",
                    "recommended_action": "rollback",
                    "verification": "recovered",
                    "evidence_call_ids": ["call-0001", "call-0003"],
                },
            ),
        ]
    )

    result = run_agent_suite(
        suite,
        prompt_set,
        provider,
        AgentRunConfig(),
        repetitions=1,
        provider_base_url="http://127.0.0.1:8000/v1",
    )

    assert result.passed is False
    assert result.aggregate["tool_call_schema_validity"] == 1.0
    assert result.aggregate["tool_call_policy_validity"] == 3 / 4
    assert result.aggregate["tool_call_validity"] == 3 / 4


def test_suite_aborts_after_first_terminal_provider_failure(suite: BenchmarkSuite) -> None:
    prompt_set = load_prompt_set(ROOT / "benchmarks" / "model_prompts_v1.json")

    result = run_agent_suite(
        suite,
        prompt_set,
        FailingProvider(),
        AgentRunConfig(),
        repetitions=2,
        provider_base_url="http://127.0.0.1:8000/v1",
    )

    assert result.passed is False
    assert result.aborted is True
    assert result.total_runs == 1
    assert result.expected_runs == 16
    assert result.aggregate["unattempted_runs"] == 15
    assert result.aggregate["infrastructure_failures"] == 1
    assert (
        result.abort_reason == "S01_BAD_DEPLOY_DIAGNOSIS repetition 1: "
        "transport_error: server unavailable"
    )
