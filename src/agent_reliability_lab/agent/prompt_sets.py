"""Loading and cross-validating agent-facing prompt sets."""

from __future__ import annotations

from pathlib import Path

from agent_reliability_lab.agent.models import AgentPromptSet
from agent_reliability_lab.domain.events import ToolEffect
from agent_reliability_lab.tasks.models import BenchmarkSuite
from agent_reliability_lab.tools.registry import build_registry


def load_prompt_set(path: Path) -> AgentPromptSet:
    return AgentPromptSet.model_validate_json(path.read_text(encoding="utf-8"))


def validate_prompt_set(prompt_set: AgentPromptSet, suite: BenchmarkSuite) -> None:
    if prompt_set.suite_id != suite.suite_id:
        raise ValueError(f"prompt set targets {prompt_set.suite_id}, but suite is {suite.suite_id}")
    tasks_by_id = {task.task_id: task for task in suite.tasks}
    suite_task_ids = set(tasks_by_id)
    unknown = set(prompt_set.tasks) - suite_task_ids
    if unknown:
        raise ValueError(f"prompt set references unknown tasks: {sorted(unknown)}")

    registry = build_registry()
    for task_id, prompt in prompt_set.tasks.items():
        task = tasks_by_id[task_id]
        required = set(prompt.required_tools)
        evidence = set(prompt.required_evidence_tools)
        call_tools = {call.tool_name for call in prompt.required_calls}
        retry_tools = {retry.tool_name for retry in prompt.required_retries}
        referenced = required | evidence | call_tools | retry_tools | set(prompt.forbidden_tools)
        unknown_tools = referenced - set(registry)
        if unknown_tools:
            raise ValueError(f"{task_id} references unknown tools: {sorted(unknown_tools)}")
        unavailable = (required | evidence | call_tools | retry_tools) - set(task.allowed_tools)
        if unavailable:
            raise ValueError(f"{task_id} requires unavailable tools: {sorted(unavailable)}")
        if not evidence.issubset(required):
            raise ValueError(f"{task_id} evidence tools must also be required tools")
        non_read_evidence = {
            name for name in evidence if registry[name].effect is not ToolEffect.READ
        }
        if non_read_evidence:
            raise ValueError(
                f"{task_id} evidence tools must be read-only: {sorted(non_read_evidence)}"
            )
        uncitable = {
            call.tool_name
            for call in prompt.required_calls
            if call.must_be_cited and registry[call.tool_name].effect is not ToolEffect.READ
        }
        if uncitable:
            raise ValueError(f"{task_id} requires non-read calls as evidence: {sorted(uncitable)}")
        uncitable_retries = {
            retry.tool_name
            for retry in prompt.required_retries
            if retry.success_must_be_cited
            and registry[retry.tool_name].effect is not ToolEffect.READ
        }
        if uncitable_retries:
            raise ValueError(
                f"{task_id} requires non-read retries as evidence: {sorted(uncitable_retries)}"
            )
        overlap = required & set(prompt.forbidden_tools)
        if overlap:
            raise ValueError(f"{task_id} both requires and forbids tools: {sorted(overlap)}")
        if prompt.max_tool_calls is not None and prompt.max_tool_calls < len(required):
            raise ValueError(
                f"{task_id} max_tool_calls must cover all required tools: "
                f"{prompt.max_tool_calls} < {len(required)}"
            )
