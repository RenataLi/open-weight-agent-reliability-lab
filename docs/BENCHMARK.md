# Benchmark contract

## Schema

`benchmarks/smoke_suite.json` is validated as a strict Pydantic model with unknown fields rejected.
It contains:

- reusable deterministic world fixtures;
- task-level fault schedules;
- per-task tool allowlists, forbidden intents, and call budgets;
- reference tool trajectories;
- exact expected statuses, error codes, state, approvals, attempts, and report.

The schema version for milestone 1 is `1.0`.

## What “passed” means

A reference task passes only when all of the following match:

- tool status sequence;
- typed error-code sequence;
- active deployment for every service;
- selected data-source and approval states;
- committed world-revision delta;
- policy-denial, commit, and replay counts;
- underlying tool-attempt counts;
- structured incident report;
- final business-state hash and trace hash on an independent replay.

Text similarity and an LLM judge are not used.

## Reference trajectories

Reference trajectories are executable benchmark-contract tests. They prove that a task is
possible and that its grader observes the intended behavior. They are not intended to represent
an intelligent agent or a publishable model result.

## Future agent evaluation

Later milestones will replace the reference trajectory with model-generated tool calls while
keeping the same simulator and exact evaluator. Additional metrics will include:

- final-state success;
- tool and argument correctness;
- prohibited-action rate;
- recovery after transient and post-commit faults;
- idempotency violations;
- redundant calls and retries;
- success@1 and repeated-run stability;
- TTFT, p95 end-to-end latency, token usage, throughput, and VRAM.
