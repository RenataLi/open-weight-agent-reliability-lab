from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from agent_reliability_lab.domain.models import ExecutionLimits
from agent_reliability_lab.simulator.engine import Simulator
from agent_reliability_lab.tasks.evaluator import load_suite
from agent_reliability_lab.tasks.models import BenchmarkSuite, BenchmarkTask

SUITE_PATH = Path(__file__).parents[1] / "benchmarks" / "smoke_suite.json"


@pytest.fixture(scope="session")
def suite() -> BenchmarkSuite:
    return load_suite(SUITE_PATH)


@pytest.fixture
def task_by_id(suite: BenchmarkSuite) -> Callable[[str], BenchmarkTask]:
    lookup = {task.task_id: task for task in suite.tasks}
    return lookup.__getitem__


@pytest.fixture
def simulator_for(
    suite: BenchmarkSuite,
) -> Callable[[BenchmarkTask], Simulator]:
    def factory(task: BenchmarkTask) -> Simulator:
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
        )

    return factory
