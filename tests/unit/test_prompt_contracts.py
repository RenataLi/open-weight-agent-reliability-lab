from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from agent_reliability_lab.agent.loop import _task_message
from agent_reliability_lab.agent.models import (
    AgentPolicy,
    AgentTaskPrompt,
    PromptContractVersion,
)
from agent_reliability_lab.agent.prompting import system_prompt
from agent_reliability_lab.tasks.models import BenchmarkTask


def _manifest_string_digest(value: str) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_v1_system_prompt_remains_frozen_byte_for_byte() -> None:
    prompt = system_prompt(AgentPolicy.POLICY_AWARE, PromptContractVersion.V1)

    assert _manifest_string_digest(prompt) == (
        "4843d9ffe75650553e19b9117558685a38436f043a1c089bd8add8e5300e716e"
    )
    assert "Reporting contract:" not in prompt


def test_v2_adds_only_general_reporting_rules() -> None:
    prompt = system_prompt(AgentPolicy.POLICY_AWARE, PromptContractVersion.V2)

    assert "recommended_action" in prompt
    assert "evidence_call_ids" in prompt
    assert "successful READ tool results" in prompt
    for leaked_identifier in ("S06", "ranker-prod", "deploy-001", "approval-10-good"):
        assert leaked_identifier not in prompt


def test_opaque_case_handle_replaces_semantic_task_id(
    task_by_id: Callable[[str], BenchmarkTask],
) -> None:
    task = task_by_id("S06_APPROVED_ROLLBACK")
    prompt = AgentTaskPrompt(
        prompt="Resolve the incident.",
        case_handle="case-a1b2c3d4",
    )

    payload = json.loads(_task_message(task, prompt))

    assert payload["case_handle"] == "case-a1b2c3d4"
    assert "task_id" not in payload
    assert "S06" not in json.dumps(payload)


def test_legacy_task_message_keeps_frozen_task_id(
    task_by_id: Callable[[str], BenchmarkTask],
) -> None:
    task = task_by_id("S06_APPROVED_ROLLBACK")

    payload = json.loads(_task_message(task, AgentTaskPrompt(prompt="Resolve the incident.")))

    assert payload["task_id"] == "S06_APPROVED_ROLLBACK"
    assert "case_handle" not in payload
