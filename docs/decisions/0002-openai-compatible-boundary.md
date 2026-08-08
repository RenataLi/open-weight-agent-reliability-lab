# ADR 0002: Use a narrow OpenAI-compatible HTTP boundary

- **Status:** Accepted
- **Date:** 2026-08-08

## Context

Milestone 1 deliberately separated the deterministic simulator from any LLM. Milestone 2 needs to
exercise a real local tool-calling model while preserving that isolation and keeping model-server
failures distinguishable from agent and simulator failures.

The target workstation has an NVIDIA RTX 5090, and the first server is vLLM. Future experiments
may use another model or serving engine. The benchmark should not import a heavyweight inference
runtime into its Python process or expose simulator internals to provider-specific response types.

## Decision

Introduce a small internal `ChatProvider` protocol and implement it with synchronous `httpx`
against the OpenAI-compatible Chat Completions endpoint.

The boundary uses project-owned Pydantic models for messages, tools, completions, usage, and
errors. Provider-specific extra response fields are ignored, while required response structure is
validated. Expected failures are normalized to stable categories with explicit retryability.

Execute the agent loop sequentially and fail closed:

- request `parallel_tool_calls: false` and enforce no more than one call per response;
- reject duplicate call IDs and malformed or ambiguous JSON arguments;
- never repair model-generated arguments;
- terminate a task on provider failure instead of inventing a response;
- route every accepted action through the existing typed simulator boundary.

Run the model server out of process. Pin the first experiment to
[`Qwen/Qwen3.5-9B`](https://huggingface.co/Qwen/Qwen3.5-9B/commit/cc5442c03a5c0bff0bd4c6888d9a40029c637733)
at commit `cc5442c03a5c0bff0bd4c6888d9a40029c637733` and
[`vllm/vllm-openai:v0.26.0`](https://hub.docker.com/r/vllm/vllm-openai/tags) for Linux/amd64 at
`sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b`.
The server interface follows vLLM's
[OpenAI-compatible API](https://docs.vllm.ai/en/v0.26.0/serving/online_serving/openai_compatible_server/)
and [tool-calling contract](https://docs.vllm.ai/en/v0.26.0/features/tool_calling/).

## Consequences

### Positive

- The deterministic core remains independent of model weights, CUDA, and server lifecycle.
- Mock transports can test the complete boundary without a GPU or network service.
- Other compatible local servers can be evaluated behind the same internal protocol.
- Sequential actions have unambiguous state and approval ordering.
- Strict parsing prevents silent repair from inflating tool-call reliability.
- Provider, agent, policy, and simulator failures remain separately observable.

### Trade-offs

- The runner is synchronous and does not measure streaming time to first token.
- Parallel tool calls are unsupported, reducing throughput but simplifying causal evaluation.
- Only the first completion choice and the implemented protocol subset are accepted.
- "OpenAI-compatible" does not guarantee identical behavior across servers; conformance is
  established by tests for the fields this project consumes.
- Seed and zero temperature improve control but do not guarantee bitwise-deterministic GPU output.

## Alternatives considered

### Load Transformers directly in the benchmark process

Rejected for this milestone because it would couple simulator dependencies, GPU allocation, model
lifecycle, and inference implementation. It would also make provider failures harder to isolate.

### Depend on a vendor SDK in the core

Rejected because the implemented HTTP surface is small and direct `httpx` requests keep the
audited boundary explicit. A future SDK-backed adapter can still implement `ChatProvider`.

### Accept parallel tool calls

Rejected because protected writes and fault injection require deterministic ordering. Parallel
execution would need a separate concurrency and transaction contract.

### Repair malformed tool arguments

Rejected because repair changes the measured system and can hide invalid model output. The runner
returns a structured argument error and records the original payload instead.

## Validation status

The boundary, loop, outcome oracle, and persistence layer are implemented and testable without a
real model. No empirical Qwen score is attached to this decision; that requires a completed,
retained GPU-run artifact set.
