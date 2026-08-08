"""Shared construction of an isolated simulator for one benchmark task."""

from __future__ import annotations

from agent_reliability_lab.domain.models import ExecutionLimits
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.models import BenchmarkSuite, BenchmarkTask
from agent_reliability_lab.tools.base import ToolSpec


def build_task_simulator(
    suite: BenchmarkSuite,
    task: BenchmarkTask,
    *,
    registry: dict[str, ToolSpec] | None = None,
) -> Simulator:
    scenario = suite.fixtures[task.fixture_id].model_copy(
        deep=True,
        update={"faults": task.faults},
    )
    return Simulator(
        scenario,
        principal_id=task.principal_id,
        limits=ExecutionLimits(
            allowed_tools=task.allowed_tools,
            forbidden_intents=task.forbidden_intents,
            max_tool_calls=task.max_tool_calls,
        ),
        registry=registry,
    )
