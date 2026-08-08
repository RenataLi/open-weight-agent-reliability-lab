# Local model provider

## Purpose

The provider layer connects the deterministic benchmark to a local tool-calling model without
coupling the simulator to a particular inference engine. The implemented transport is a small,
synchronous subset of the OpenAI Chat Completions protocol served by vLLM.

This document describes the implemented boundary and the pinned single-GPU deployment. It does
not report a model score; no retained real-model result exists yet.

## HTTP contract

`OpenAICompatibleProvider` normalizes the configured base URL and sends `POST` requests to:

```text
<base-url>/chat/completions
```

The default local base URL is `http://127.0.0.1:8000/v1`, producing
`http://127.0.0.1:8000/v1/chat/completions`. This is the Chat API provided by vLLM's
[OpenAI-compatible server](https://docs.vllm.ai/en/v0.26.0/serving/online_serving/openai_compatible_server/).

Every request contains:

- the configured model identifier;
- typed conversation messages;
- JSON Schema function tools derived from the simulator registry;
- temperature and optional seed;
- run-level `top_p`, `max_tokens`, and tool-choice settings.

The agent runner additionally requests:

```json
{
  "parallel_tool_calls": false,
  "tool_choice": "auto",
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

The provider reserves the fields it owns, so caller-supplied extra parameters cannot replace
`model`, `messages`, `tools`, `seed`, or `temperature`.

## Response validation

Compatible servers may add response fields, which the wire models ignore. The boundary still
requires and validates the fields used by the experiment:

- at least one choice;
- an assistant message;
- structurally valid function tool calls and unique call IDs within the response;
- non-negative token usage with a consistent total, when usage is present;
- a finite, non-negative measured latency.

Only the first completion choice is consumed. The agent loop independently enforces at most one
tool call per turn and rejects reuse of a provider call ID across turns.

Tool arguments remain encoded JSON until they reach the runner. They must parse as exactly one
object. Duplicate keys, `NaN`, positive or negative infinity, and non-object values are rejected;
the benchmark does not silently repair model output.

## Failure model

Expected inference failures cross the provider boundary as stable `ProviderError` categories:

| Code | Meaning | Retryable metadata |
| --- | --- | --- |
| `invalid_request` | empty messages or conflicting client options | no |
| `timeout` | model request timed out | yes |
| `transport_error` | connection-level transport failure | yes |
| `http_error` | non-success HTTP response | yes for 408, 409, 425, 429, and 5xx |
| `invalid_response` | malformed or incompatible success payload | no |

The provider retries retryable timeout, transport, HTTP 408/409/425/429, and 5xx failures with an
explicit fixed backoff. CLI `--provider-attempts N` permits at most `N` identical POST requests;
HTTPX's hidden transport retries are disabled. Successful completions retain attempt count, prior
error categories, and full-span latency. Exhaustion retains the same provenance on the terminal
provider error and terminates the task as an infrastructure failure.

Retry error codes describe only failures followed by another request; attempt error codes also
include the final terminal failure. Failed model turns retain measured elapsed latency, so an
exhausted timeout cannot appear as a zero-latency benchmark run.

Before creating a run directory, the CLI validates `GET <base-url>/models` and requires the exact
configured model identifier. Completion responses that claim a different model are rejected.
The HTTP client uses `trust_env=False`, so ambient proxy variables cannot silently reroute the
loopback benchmark or its optional authorization header.

Simulator tool failures are separate. They are typed tool results inside the conversation, so a
policy variant can safely decide whether a read should be retried or an idempotent write replayed.

## Pinned inference stack

| Component | Pinned value | Source |
| --- | --- | --- |
| Model | `Qwen/Qwen3.5-9B` | [official model repository](https://huggingface.co/Qwen/Qwen3.5-9B) |
| Model revision | `cc5442c03a5c0bff0bd4c6888d9a40029c637733` | [immutable revision with the multi-turn chat-template fix](https://huggingface.co/Qwen/Qwen3.5-9B/commit/cc5442c03a5c0bff0bd4c6888d9a40029c637733) |
| Server image | `vllm/vllm-openai:v0.26.0` | [official Docker Hub tags](https://hub.docker.com/r/vllm/vllm-openai/tags) |
| Linux/amd64 digest | `sha256:770fe65b2c73ee74a5c42165cf3433de4048cc2cd9c57a937ca4e35aba5aa87b` | recorded in `deploy/compose.gpu.yml` |
| Tool parser | `qwen3_coder` | [vLLM tool calling](https://docs.vllm.ai/en/v0.26.0/features/tool_calling/) |
| Reasoning parser | `qwen3` | [vLLM reasoning outputs](https://docs.vllm.ai/en/v0.26.0/features/reasoning_outputs/) |

The tag documents the intended vLLM release; the digest prevents a later tag update from silently
changing the container used by this experiment. The full Hugging Face commit prevents `main` from
silently changing model weights or configuration.

## RTX 5090 deployment profile

The checked-in Compose service reserves one NVIDIA GPU following Docker's
[Compose GPU reservation syntax](https://docs.docker.com/compose/how-tos/gpu-support/). It is
configured for the 32 GB GeForce RTX 5090 described in NVIDIA's
[official specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/):

- BF16 model dtype;
- maximum model length of 32,768 tokens;
- GPU-memory utilization target of `0.80`;
- one concurrent sequence;
- language-model-only loading to avoid reserving VRAM for an unused vision encoder;
- prefix caching disabled for the first low-concurrency baseline;
- automatic tool choice with Qwen tool and reasoning parsers;
- host port bound only to `127.0.0.1:8000`;
- Hugging Face cache persisted under `models/huggingface`.

The 80% target deliberately leaves headroom for the display, CUDA runtime, and transient
allocations. It is an experimental default for this workstation, not a guarantee against
out-of-memory errors on every driver or runtime combination.

On Windows, Docker documents that GPU access is available with its
[WSL 2 backend](https://docs.docker.com/desktop/features/gpu/). Keep WSL, Docker Desktop, and the
NVIDIA driver current before diagnosing model-level failures.

## Start, run, and stop

Run these commands from the project root in PowerShell:

```powershell
# Optional: validate the resolved deployment before downloading anything.
docker compose --project-directory . -f .\deploy\compose.gpu.yml config

# Start vLLM. The first start downloads the pinned weights into the project cache.
.\scripts\start_model.ps1

# Observe model loading and server startup.
docker logs -f agent-reliability-vllm

# Check the local health endpoint in a second terminal.
Invoke-RestMethod http://127.0.0.1:8000/health

# Run a one-task canary before the complete suite.
.\scripts\run_model_benchmark.ps1 -TaskId S01_BAD_DEPLOY_DIAGNOSIS

# Execute the eight model tasks. A unique run ID is generated automatically.
.\scripts\run_model_benchmark.ps1 -Repetitions 1

# Stop the server and release GPU memory. Cached weights remain in the project.
.\scripts\stop_model.ps1
```

For repeated stability measurement, choose a fresh run ID and increase repetitions:

```powershell
.\scripts\run_model_benchmark.ps1 `
  -Repetitions 5 `
  -RunId qwen35-policy-aware-r5
```

An existing run directory is never overwritten. The benchmark command returns a distinct status
for outcome failure and provider infrastructure failure; inspect `result.json` and `events.jsonl`
before attributing a failure to model behavior.

## Authentication and exposure

Authentication is optional for the loopback deployment. If a compatible server requires it, set
`VLLM_API_KEY` in the process environment; the client sends it as a bearer token. The value is
never written to run artifacts. `.env.example` documents the variable without supplying a secret.

The manifest sanitizes the recorded provider URL and stores only `api_key_present: true|false`.
The supplied server port is loopback-only. Changing the binding or pointing the client at a remote
server changes the threat model and should be documented as a separate experiment.

## Reproducibility limits

The pinned model, image, generation settings, input hashes, and single-sequence configuration
reduce uncontrolled variation. They do not promise bitwise-identical GPU inference. Results may
still vary with the GPU, NVIDIA driver, Docker/WSL stack, CUDA kernels, and inference scheduling.

For any published result, retain the complete run directory and report the hardware, driver,
model revision, image digest, policy, prompt-set hash, benchmark hash, and repetition count.

## Adding another compatible provider

Implement the small `ChatProvider` protocol rather than modifying the simulator. A provider must:

1. expose its configured model identifier;
2. accept typed messages and function tools;
3. return the provider-neutral `ChatCompletion` model;
4. translate expected failures into `ProviderError` with correct retryability metadata;
5. release owned resources through `close()`.

This preserves the agent loop, exact outcome oracle, and artifact schema across inference engines.
