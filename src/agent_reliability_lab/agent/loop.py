"""Fail-closed sequential loop for an OpenAI-compatible tool-calling model."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

from pydantic import JsonValue

from agent_reliability_lab.agent.evaluation import (
    count_unsafe_action_attempts,
    evaluate_agent_outcome,
)
from agent_reliability_lab.agent.models import (
    AgentRunConfig,
    AgentTaskPrompt,
    AgentTaskResult,
    ExecutedToolCall,
    ModelTurnRecord,
    PromptContractVersion,
    TerminationReason,
    build_request_extra_body,
)
from agent_reliability_lab.agent.prompting import system_prompt
from agent_reliability_lab.domain.events import ToolStatus
from agent_reliability_lab.providers.base import ChatProvider, ProviderError
from agent_reliability_lab.providers.models import ChatMessage, ChatRole
from agent_reliability_lab.providers.schema_adapter import to_chat_tools
from agent_reliability_lab.tasks.models import BenchmarkSuite, BenchmarkTask
from agent_reliability_lab.tasks.runtime import build_task_simulator


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def parse_tool_arguments(raw: str) -> dict[str, JsonValue]:
    """Parse exactly one strict JSON object without repair or duplicate keys."""

    value = json.loads(
        raw,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _task_message(task: BenchmarkTask, prompt: AgentTaskPrompt) -> str:
    tool_call_budget = prompt.max_tool_calls or task.max_tool_calls
    payload: dict[str, object] = {
        "kind": "benchmark_task",
        "prompt": prompt.prompt,
        "constraints": {
            "max_tool_calls": tool_call_budget,
            "forbidden_intents": sorted(task.forbidden_intents),
        },
    }
    if prompt.case_handle is None:
        payload["task_id"] = task.task_id
    else:
        payload["case_handle"] = prompt.case_handle
    return _canonical_json(payload)


class AgentRunner:
    """Run one clean benchmark world until report, failure, or a hard limit."""

    def __init__(
        self,
        provider: ChatProvider,
        config: AgentRunConfig,
        *,
        contract_version: PromptContractVersion = PromptContractVersion.V1,
    ) -> None:
        self._provider = provider
        self._config = config
        self._contract_version = contract_version

    def run_task(
        self,
        suite: BenchmarkSuite,
        task: BenchmarkTask,
        prompt: AgentTaskPrompt,
        *,
        repetition: int = 1,
        event_sink: Callable[[dict[str, JsonValue]], None] | None = None,
    ) -> AgentTaskResult:
        tool_call_budget = prompt.max_tool_calls or task.max_tool_calls
        simulator = build_task_simulator(
            suite,
            task,
            max_tool_calls=tool_call_budget,
        )
        initial_revision = simulator.world.revision
        messages = [
            ChatMessage(
                role=ChatRole.SYSTEM,
                content=system_prompt(self._config.policy, self._contract_version),
            ),
            ChatMessage(role=ChatRole.USER, content=_task_message(task, prompt)),
        ]
        tools = to_chat_tools(simulator.tool_schemas())
        model_turns: list[ModelTurnRecord] = []
        executed_calls: list[ExecutedToolCall] = []
        provider_call_ids: set[str] = set()
        termination = TerminationReason.MAX_TURNS
        infrastructure_error: str | None = None

        for turn in range(1, self._config.max_turns + 1):
            try:
                completion = self._provider.complete(
                    messages,
                    tools=tools,
                    extra_body=build_request_extra_body(self._config),
                )
            except ProviderError as exc:
                termination = TerminationReason.PROVIDER_ERROR
                infrastructure_error = f"{exc.code.value}: {exc.message}"
                model_turns.append(
                    ModelTurnRecord(
                        turn=turn,
                        latency_ms=exc.latency_ms or 0.0,
                        finish_reason="provider_error",
                        provider_attempts=exc.provider_attempts,
                        provider_retry_error_codes=exc.retry_error_codes,
                        provider_attempt_error_codes=exc.attempt_error_codes,
                        provider_error_code=exc.code.value,
                    )
                )
                if event_sink is not None:
                    event_sink(
                        {
                            "kind": "provider_error",
                            "turn": turn,
                            "code": exc.code.value,
                            "latency_ms": exc.latency_ms or 0.0,
                            "provider_attempts": exc.provider_attempts,
                            "provider_retry_error_codes": list(exc.retry_error_codes),
                            "provider_attempt_error_codes": list(exc.attempt_error_codes),
                        }
                    )
                break

            usage = completion.usage
            calls = completion.message.tool_calls
            model_turns.append(
                ModelTurnRecord(
                    turn=turn,
                    latency_ms=completion.latency_ms,
                    finish_reason=completion.finish_reason,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    assistant_content=completion.message.content,
                    tool_call_count=len(calls),
                    provider_attempts=completion.provider_attempts,
                    provider_retry_error_codes=completion.retry_error_codes,
                    provider_attempt_error_codes=completion.retry_error_codes,
                )
            )
            if event_sink is not None:
                event_sink(
                    {
                        "kind": "model_turn",
                        "turn": turn,
                        "latency_ms": completion.latency_ms,
                        "finish_reason": completion.finish_reason,
                        "tool_call_count": len(calls),
                        "provider_attempts": completion.provider_attempts,
                        "provider_retry_error_codes": list(completion.retry_error_codes),
                        "provider_attempt_error_codes": list(completion.retry_error_codes),
                    }
                )

            if completion.message.role is not ChatRole.ASSISTANT:
                termination = TerminationReason.INVALID_MODEL_OUTPUT
                break
            if len(calls) > 1:
                executed_calls.extend(
                    ExecutedToolCall(
                        turn=turn,
                        provider_call_id=call.id,
                        tool_name=call.function.name,
                        raw_arguments=call.function.arguments,
                        parse_error="multiple tool calls in one turn",
                    )
                    for call in calls
                )
                if event_sink is not None:
                    event_sink(
                        {
                            "kind": "rejected_tool_calls",
                            "turn": turn,
                            "reason": "multiple tool calls in one turn",
                            "calls": [
                                {"id": call.id, "tool_name": call.function.name} for call in calls
                            ],
                        }
                    )
                termination = TerminationReason.INVALID_MODEL_OUTPUT
                break
            messages.append(completion.message)
            if not calls:
                termination = TerminationReason.NO_TOOL_CALL
                break

            call = calls[0]
            if call.id in provider_call_ids:
                executed_calls.append(
                    ExecutedToolCall(
                        turn=turn,
                        provider_call_id=call.id,
                        tool_name=call.function.name,
                        raw_arguments=call.function.arguments,
                        parse_error="duplicate provider tool-call ID",
                    )
                )
                termination = TerminationReason.INVALID_MODEL_OUTPUT
                break
            provider_call_ids.add(call.id)

            try:
                arguments = parse_tool_arguments(call.function.arguments)
            except (TypeError, ValueError) as exc:
                error_text = str(exc)
                executed_calls.append(
                    ExecutedToolCall(
                        turn=turn,
                        provider_call_id=call.id,
                        tool_name=call.function.name,
                        raw_arguments=call.function.arguments,
                        parse_error=error_text,
                    )
                )
                messages.append(
                    ChatMessage(
                        role=ChatRole.TOOL,
                        tool_call_id=call.id,
                        name=call.function.name,
                        content=_canonical_json(
                            {
                                "status": "error",
                                "error": {
                                    "code": "invalid_model_tool_arguments",
                                    "message": error_text,
                                    "retryable": False,
                                },
                            }
                        ),
                    )
                )
                continue

            result = simulator.call_tool(call.function.name, arguments)
            executed_calls.append(
                ExecutedToolCall(
                    turn=turn,
                    provider_call_id=call.id,
                    tool_name=call.function.name,
                    raw_arguments=call.function.arguments,
                    arguments=arguments,
                    result=result,
                )
            )
            if event_sink is not None:
                event_sink(
                    {
                        "kind": "tool_result",
                        "turn": turn,
                        "provider_call_id": call.id,
                        "tool_name": call.function.name,
                        "status": result.status,
                        "simulator_call_id": result.call_id,
                    }
                )
            messages.append(
                ChatMessage(
                    role=ChatRole.TOOL,
                    tool_call_id=call.id,
                    name=call.function.name,
                    content=_canonical_json(result.model_dump(mode="json")),
                )
            )
            if call.function.name == "submit_incident_report" and result.status is ToolStatus.OK:
                termination = TerminationReason.REPORT_SUBMITTED
                break
            if simulator.session.call_sequence >= tool_call_budget:
                termination = TerminationReason.TOOL_BUDGET_EXHAUSTED
                break

        checks = evaluate_agent_outcome(
            task,
            prompt,
            simulator,
            initial_revision=initial_revision,
            executed_calls=executed_calls,
        )
        passed = all(check.passed for check in checks) and infrastructure_error is None
        total_latency = sum(record.latency_ms for record in model_turns)
        provider_attempts = sum(record.provider_attempts for record in model_turns)
        provider_retries = sum(record.provider_attempts - 1 for record in model_turns)
        provider_retry_codes = tuple(
            code for record in model_turns for code in record.provider_retry_error_codes
        )
        provider_attempt_error_codes = tuple(
            code for record in model_turns for code in record.provider_attempt_error_codes
        )
        return AgentTaskResult(
            task_id=task.task_id,
            repetition=repetition,
            passed=passed,
            termination_reason=termination,
            checks=checks,
            model_turns=tuple(model_turns),
            tool_calls=tuple(executed_calls),
            simulator_trace=tuple(simulator.session.trace),
            final_state_hash=simulator.state_hash,
            trace_hash=simulator.trace_hash,
            world_revision=simulator.world.revision,
            total_latency_ms=total_latency,
            prompt_tokens=sum(record.prompt_tokens for record in model_turns),
            completion_tokens=sum(record.completion_tokens for record in model_turns),
            unsafe_action_attempts=count_unsafe_action_attempts(
                simulator,
                prompt,
                executed_calls,
            ),
            committed_effects=sum(trace.committed for trace in simulator.session.trace),
            replayed_calls=sum(trace.replayed for trace in simulator.session.trace),
            provider_attempts=provider_attempts,
            provider_retries=provider_retries,
            provider_retry_error_codes=provider_retry_codes,
            provider_attempt_error_codes=provider_attempt_error_codes,
            infrastructure_error=infrastructure_error,
        )
