"""Exact state- and trace-based evaluation without an LLM judge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from agent_reliability_lab.domain.events import PolicyDecision, ToolResult, ToolStatus
from agent_reliability_lab.domain.models import ExecutionLimits, IncidentReport
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import (
    BenchmarkSuite,
    BenchmarkTask,
    EvaluationCheck,
    SuiteRunResult,
    TaskRunResult,
)
from agent_reliability_lab.tools.registry import build_registry


def load_suite(path: Path) -> BenchmarkSuite:
    """Load one UTF-8 JSON suite through the strict public schema."""

    text = path.read_text(encoding="utf-8")
    return BenchmarkSuite.model_validate_json(text)


def validate_suite_contract(suite: BenchmarkSuite) -> None:
    registry = build_registry()
    for task in suite.tasks:
        unknown_steps = {
            step.tool_name for step in task.reference_trajectory if step.tool_name not in registry
        }
        if unknown_steps:
            raise ValueError(f"task {task.task_id} uses unknown tools: {sorted(unknown_steps)}")
        undeclared = {step.tool_name for step in task.reference_trajectory} - set(
            task.allowed_tools
        )
        if undeclared:
            raise ValueError(
                f"task {task.task_id} reference trajectory uses disallowed tools: "
                f"{sorted(undeclared)}"
            )
        scenario = suite.fixtures[task.fixture_id].model_copy(
            deep=True,
            update={"faults": task.faults},
        )
        Simulator(
            scenario,
            principal_id=task.principal_id,
            limits=ExecutionLimits(
                allowed_tools=task.allowed_tools,
                forbidden_intents=task.forbidden_intents,
                max_tool_calls=task.max_tool_calls,
            ),
            registry=registry,
        )


def _json_value(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False)))


def _check(name: str, expected: object, observed: object) -> EvaluationCheck:
    return EvaluationCheck(
        name=name,
        passed=expected == observed,
        expected=_json_value(expected),
        observed=_json_value(observed),
    )


def _execute(suite: BenchmarkSuite, task: BenchmarkTask) -> tuple[Simulator, list[ToolResult]]:
    scenario = suite.fixtures[task.fixture_id].model_copy(
        deep=True,
        update={"faults": task.faults},
    )
    simulator = Simulator(
        scenario,
        principal_id=task.principal_id,
        limits=ExecutionLimits(
            allowed_tools=task.allowed_tools,
            forbidden_intents=task.forbidden_intents,
            max_tool_calls=task.max_tool_calls,
        ),
    )
    results = [
        simulator.call_tool(step.tool_name, step.arguments) for step in task.reference_trajectory
    ]
    return simulator, results


def run_task(suite: BenchmarkSuite, task: BenchmarkTask) -> TaskRunResult:
    initial_revision = suite.fixtures[task.fixture_id].world.revision
    simulator, raw_results = _execute(suite, task)
    results = list(raw_results)
    statuses = tuple(result.status for result in results)
    error_codes = tuple(
        result.error.code if result.error is not None else None for result in results
    )
    expected = task.expected
    checks: list[EvaluationCheck] = [
        _check("status_sequence", list(expected.status_sequence), list(statuses)),
        _check("error_codes", list(expected.error_codes), list(error_codes)),
        _check(
            "active_deployments",
            expected.active_deployments,
            {
                service_id: service.active_deployment_id
                for service_id, service in sorted(simulator.world.services.items())
            },
        ),
        _check(
            "data_source_statuses",
            expected.data_source_statuses,
            {
                source_id: source.status
                for source_id, source in sorted(simulator.world.data_sources.items())
                if source_id in expected.data_source_statuses
            },
        ),
        _check(
            "approval_statuses",
            expected.approval_statuses,
            {
                approval_id: approval.status
                for approval_id, approval in sorted(simulator.world.approvals.items())
                if approval_id in expected.approval_statuses
            },
        ),
        _check(
            "world_revision_delta",
            expected.world_revision_delta,
            simulator.world.revision - initial_revision,
        ),
        _check(
            "policy_denials",
            expected.policy_denials,
            sum(
                trace.policy_decision is PolicyDecision.DENY
                or trace.status is ToolStatus.APPROVAL_REQUIRED
                for trace in simulator.session.trace
            ),
        ),
        _check(
            "committed_effects",
            expected.committed_effects,
            sum(trace.committed for trace in simulator.session.trace),
        ),
        _check(
            "replays",
            expected.replays,
            sum(trace.replayed for trace in simulator.session.trace),
        ),
        _check("attempt_counts", expected.attempt_counts, simulator.attempt_counts),
        _check(
            "report",
            expected.report.model_dump(mode="json") if expected.report else None,
            simulator.session.submitted_report.model_dump(mode="json")
            if isinstance(simulator.session.submitted_report, IncidentReport)
            else None,
        ),
    ]

    replay, replay_results = _execute(suite, task)
    replay_statuses = [result.status for result in replay_results]
    checks.append(
        _check(
            "deterministic_replay",
            {
                "state_hash": simulator.state_hash,
                "trace_hash": simulator.trace_hash,
                "statuses": list(statuses),
            },
            {
                "state_hash": replay.state_hash,
                "trace_hash": replay.trace_hash,
                "statuses": replay_statuses,
            },
        )
    )
    return TaskRunResult(
        task_id=task.task_id,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
        statuses=statuses,
        error_codes=error_codes,
        final_state_hash=simulator.state_hash,
        trace_hash=simulator.trace_hash,
        world_revision=simulator.world.revision,
    )


def run_suite(suite: BenchmarkSuite) -> SuiteRunResult:
    validate_suite_contract(suite)
    task_results = tuple(
        run_task(suite, task) for task in sorted(suite.tasks, key=lambda t: t.task_id)
    )
    passed_tasks = sum(result.passed for result in task_results)
    return SuiteRunResult(
        suite_id=suite.suite_id,
        passed=passed_tasks == len(task_results),
        passed_tasks=passed_tasks,
        total_tasks=len(task_results),
        tasks=task_results,
    )
