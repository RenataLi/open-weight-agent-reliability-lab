"""Repeated model evaluation and aggregate reliability metrics."""

from __future__ import annotations

import math
from collections.abc import Callable

from pydantic import JsonValue

from agent_reliability_lab.agent.loop import AgentRunner
from agent_reliability_lab.agent.models import (
    AgentPromptSet,
    AgentRunConfig,
    AgentSuiteResult,
    AgentTaskResult,
    ExecutedToolCall,
)
from agent_reliability_lab.agent.prompt_sets import validate_prompt_set
from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.providers.base import ChatProvider
from agent_reliability_lab.tasks.models import BenchmarkSuite


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def run_agent_suite(
    suite: BenchmarkSuite,
    prompt_set: AgentPromptSet,
    provider: ChatProvider,
    config: AgentRunConfig,
    *,
    repetitions: int,
    provider_base_url: str,
    model_revision: str | None = None,
    event_sink_factory: Callable[[str, int], Callable[[dict[str, JsonValue]], None] | None]
    | None = None,
) -> AgentSuiteResult:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    validate_prompt_set(prompt_set, suite)
    tasks_by_id = {task.task_id: task for task in suite.tasks}
    runner = AgentRunner(
        provider,
        config,
        contract_version=prompt_set.contract_version,
    )
    results: list[AgentTaskResult] = []
    expected_runs = len(prompt_set.tasks) * repetitions
    abort_reason: str | None = None
    for task_id in sorted(prompt_set.tasks):
        task = tasks_by_id[task_id]
        for repetition in range(1, repetitions + 1):
            sink = event_sink_factory(task_id, repetition) if event_sink_factory else None
            task_result = runner.run_task(
                suite,
                task,
                prompt_set.tasks[task_id],
                repetition=repetition,
                event_sink=sink,
            )
            results.append(task_result)
            if task_result.infrastructure_error is not None:
                abort_reason = (
                    f"{task_id} repetition {repetition}: {task_result.infrastructure_error}"
                )
                break
        if abort_reason is not None:
            break

    passed_runs = sum(result.passed for result in results)
    total_runs = len(results)
    latencies = [result.total_latency_ms for result in results]
    completed_latencies = [
        result.total_latency_ms for result in results if result.infrastructure_error is None
    ]
    infrastructure_latencies = [
        result.total_latency_ms for result in results if result.infrastructure_error is not None
    ]
    task_pass_counts: dict[str, JsonValue] = {
        task_id: sum(result.passed for result in results if result.task_id == task_id)
        for task_id in sorted(prompt_set.tasks)
    }
    tool_calls = [call for result in results for call in result.tool_calls]
    parse_valid_calls = sum(call.arguments is not None for call in tool_calls)
    schema_invalid_codes = {"invalid_arguments", "unknown_tool", "tool_not_allowed"}

    def schema_valid(typed_call: ExecutedToolCall) -> bool:
        return bool(
            typed_call.arguments is not None
            and typed_call.result is not None
            and (
                typed_call.result.error is None
                or typed_call.result.error.code not in schema_invalid_codes
            )
        )

    def policy_valid(typed_call: ExecutedToolCall) -> bool:
        return bool(
            typed_call.result is not None
            and typed_call.result.status
            not in {ToolStatus.POLICY_DENIED, ToolStatus.APPROVAL_REQUIRED}
            and (
                typed_call.result.error is None
                or typed_call.result.error.code != "tool_not_allowed"
            )
        )

    schema_valid_calls = sum(schema_valid(call) for call in tool_calls)
    policy_valid_calls = sum(policy_valid(call) for call in tool_calls)
    overall_valid_calls = sum(schema_valid(call) and policy_valid(call) for call in tool_calls)
    aggregate: dict[str, JsonValue] = {
        "pass_rate": passed_runs / total_runs if total_runs else 0.0,
        "expected_runs": expected_runs,
        "unattempted_runs": expected_runs - total_runs,
        "aborted": abort_reason is not None,
        "task_pass_counts": task_pass_counts,
        "infrastructure_failures": sum(
            result.infrastructure_error is not None for result in results
        ),
        "unsafe_action_attempts": sum(result.unsafe_action_attempts for result in results),
        "committed_effects": sum(result.committed_effects for result in results),
        "replayed_calls": sum(result.replayed_calls for result in results),
        "provider_attempts": sum(result.provider_attempts for result in results),
        "provider_retries": sum(result.provider_retries for result in results),
        "provider_retry_error_counts": {
            code: sum(result.provider_retry_error_codes.count(code) for result in results)
            for code in sorted(
                {code for result in results for code in result.provider_retry_error_codes}
            )
        },
        "provider_attempt_error_counts": {
            code: sum(result.provider_attempt_error_codes.count(code) for result in results)
            for code in sorted(
                {code for result in results for code in result.provider_attempt_error_codes}
            )
        },
        "tool_call_parse_validity": parse_valid_calls / len(tool_calls) if tool_calls else 0.0,
        "tool_call_schema_validity": schema_valid_calls / len(tool_calls) if tool_calls else 0.0,
        "tool_call_policy_validity": policy_valid_calls / len(tool_calls) if tool_calls else 0.0,
        "tool_call_validity": overall_valid_calls / len(tool_calls) if tool_calls else 0.0,
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "completed_latency_ms_p50": _percentile(completed_latencies, 0.50),
        "completed_latency_ms_p95": _percentile(completed_latencies, 0.95),
        "infrastructure_latency_ms_p50": _percentile(infrastructure_latencies, 0.50),
        "infrastructure_latency_ms_p95": _percentile(infrastructure_latencies, 0.95),
        "prompt_tokens": sum(result.prompt_tokens for result in results),
        "completion_tokens": sum(result.completion_tokens for result in results),
    }
    return AgentSuiteResult(
        suite_id=suite.suite_id,
        prompt_set_id=prompt_set.prompt_set_id,
        contract_version=prompt_set.contract_version,
        model=provider.model,
        model_revision=model_revision,
        provider_base_url=provider_base_url,
        policy=config.policy,
        repetitions=repetitions,
        passed=abort_reason is None and passed_runs == expected_runs,
        passed_runs=passed_runs,
        total_runs=total_runs,
        expected_runs=expected_runs,
        aborted=abort_reason is not None,
        abort_reason=abort_reason,
        task_results=tuple(results),
        aggregate=aggregate,
    )
