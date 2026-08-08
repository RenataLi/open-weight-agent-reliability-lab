# ADR 0001: Build the deterministic core before connecting an LLM

- Status: accepted
- Date: 2026-08-08

## Context

An LLM introduces sampling, serving, parser, and model-quality failures. Building the benchmark
and model adapter simultaneously would make it difficult to distinguish agent failure from an
invalid task, grader bug, or unsafe state transition.

## Decision

Milestone 1 contains no LLM and no network service. A scripted reference trajectory exercises the
same typed tool boundary that later model adapters will use.

## Consequences

- Simulator and evaluator behavior can be fully deterministic and tested on CPU.
- CI needs no model weights or secrets.
- Every future model result has a stable benchmark contract.
- Milestone 1 does not demonstrate language understanding by itself; that is explicit, not hidden.
