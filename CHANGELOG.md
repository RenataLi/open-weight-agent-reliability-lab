# Changelog

All notable project changes will be documented here.

## [0.2.0] - 2026-08-08

### Added

- synchronous `httpx` provider for the OpenAI-compatible Chat Completions API;
- typed chat, usage, tool-call, and stable provider-error contracts;
- explicit bounded provider retries, exact model preflight, and retry provenance;
- sequential fail-closed agent loop with strict JSON arguments and duplicate call-ID rejection;
- versioned baseline, retry-aware, idempotent, and policy-aware prompting;
- eight-task model prompt set spanning diagnosis, prompt injection, approvals, rollback, transient
  failure, and lost post-commit response;
- trajectory-independent outcome grading for exact state, report semantics, grounded call/result
  predicates, blocked unsafe selections, required behaviors, and unauthorized commits;
- repeated-run aggregation for pass rate, tool validity, safety events, effects, replays, latency,
  and token usage;
- crash-tolerant run storage with a hashed manifest, append-only JSONL event stream, and atomic
  result JSON;
- RTX 5090-oriented Docker Compose profile for vLLM 0.26.0 and Qwen3.5-9B, both pinned to immutable
  identifiers, plus PowerShell lifecycle and benchmark scripts.

### Evidence status

- No score from a real model is included in this release. A model result will be reported only
  after the pinned GPU run has completed and its artifacts have been retained.

## [0.1.0] - 2026-08-08

### Added

- deterministic incident-response world and typed tool registry;
- fail-closed approval binding and protected rollback/quarantine actions;
- atomic transitions, event ledger, idempotent replay, and conflict detection;
- deterministic before/after-effect fault injection;
- strict benchmark schema, exact evaluator, CLI, and 12-task smoke suite;
- unit and integration checks for policy, determinism, faults, schemas, and CLI behavior.
