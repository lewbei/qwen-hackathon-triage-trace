# TriageTrace Domain Glossary

## Core concepts

- **Incident**: an operational alert describing a symptom, affected service, and optional context.
- **Run**: a single agent execution against an incident in either `stateless` or `memory` mode.
- **Tool**: a read-only operational accessor such as metrics, deployments, or runbooks.
- **ActionProposal**: a suggested remediation produced by the agent. It requires human approval before simulated execution.
- **Memory**: a durable record of observation, episode, procedure, preference, or policy learned from prior runs.
- **Temporal Memory Firewall**: lifecycle and trust rules that decide which memories may be recalled and when.

## Memory lifecycle

- **Candidate**: freshly extracted, not yet promoted.
- **Active**: trusted, valid, and eligible for retrieval.
- **Quarantined**: contradicts a higher-authority source or contains an embedded instruction from untrusted content.
- **Superseded**: replaced by a newer, equally-or-more authoritative memory.
- **Expired**: past its `expires_at` timestamp.
- **Deleted**: explicitly forgotten by an operator; retained as an audit tombstone.

## Memory types

- **observation**: raw, untrusted tool output. TTL 24 hours.
- **episode**: a resolved incident summary. TTL 30 days.
- **procedure**: an approved, successful remediation. TTL 14 days.
- **preference**: an operator-stated constraint or preference. Persists until superseded or deleted.
- **policy**: a hard safety rule. Persists until superseded or deleted.

## Trust and provenance

- **Provenance**: source of a memory (tool, operator, runbook, log, etc.).
- **Authority**: a numeric trust level for provenance. Operator and approved execution are highest; logs are lowest.
- **Source timestamp**: when the memory was produced.
- **Valid_from / expires_at**: temporal validity window.
