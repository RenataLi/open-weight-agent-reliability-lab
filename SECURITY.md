# Security policy

The project treats tool schemas, policy decisions, approval bindings, idempotency keys, and
artifact integrity as security boundaries—even though the benchmark runs against an isolated,
deterministic simulator rather than real infrastructure.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting flow from the repository **Security** tab. Include a
minimal reproduction, the affected version or commit, and sanitized evidence. Please do not open
a public issue for credentials, private traces, provider tokens, or an unpatched vulnerability.

Reports are acknowledged as quickly as possible and assessed against the same fail-closed safety
invariants used by the benchmark.

## Supported version

Security fixes target the latest release and the `main` branch.

## Trust boundary

- The model can propose only task-scoped, schema-validated simulator calls.
- Protected writes require an exact, unexpired approval binding.
- Retrieved runbooks and tool output are treated as untrusted data.
- Raw transcripts, model weights, provider secrets, and local run artifacts are excluded from Git.
