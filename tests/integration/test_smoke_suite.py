from __future__ import annotations

from agent_reliability_lab.tasks.evaluator import run_suite
from agent_reliability_lab.tasks.models import BenchmarkSuite


def test_all_reference_trajectories_pass_exact_evaluation(suite: BenchmarkSuite) -> None:
    result = run_suite(suite)
    assert result.passed
    assert result.passed_tasks == 12
    assert result.total_tasks == 12
    assert all(task.passed for task in result.tasks)


def test_entire_suite_is_bitwise_deterministic(suite: BenchmarkSuite) -> None:
    first = run_suite(suite)
    second = run_suite(suite)
    assert first.model_dump_json() == second.model_dump_json()
