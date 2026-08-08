# Security

Milestone 1 is an in-memory simulator and cannot reach a real deployment. Nevertheless, its
policy contracts are treated as security boundaries.

## Reporting

Do not open a public issue containing credentials, private traces, or production data. Until a
dedicated security contact is published, report a suspected vulnerability privately to the
repository owner.

## Explicit non-goals

- The simulator is not an authorization system for real infrastructure.
- Approval identifiers are deterministic benchmark data, not production secrets.
- Runbooks and tool outputs are intentionally untrusted inputs.
