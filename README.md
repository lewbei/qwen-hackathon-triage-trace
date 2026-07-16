# TriageTrace — A Temporal Memory Firewall for Incident Agents

**Track 1: MemoryAgent** — Qwen Hackathon 2026

TriageTrace is a temporal-memory incident-response agent that learns from validated outcomes and refuses poisoned, contradictory, or obsolete memories.

## Hosted Demo

- Live URL: `https://TODO-alibaba-ecs-url`
- Public repository: `https://github.com/TODO/triage-trace`

## Quickstart

```bash
cp .env.example .env
# Add your Qwen Cloud API key to .env
docker compose up --build
```

The dashboard is at `http://localhost:5173` and the API at `http://localhost:8000`.

## What it proves

1. A stateless Qwen agent proposes an unsuitable remediation.
2. An operator corrects it and resolves the incident; the validated lesson and preference become durable memories.
3. A new browser session receives a related incident and recalls the correct experience under an 800-token memory budget.
4. A newer runbook supersedes the old fix, while a malicious instruction embedded in logs is quarantined.
5. An evaluation dashboard shows improvement over the identical stateless Qwen baseline.

## Benchmarks

Results are committed to `evaluations/latest.json` and rendered below:

| Metric | Stateless | Memory | Δ |
|---|---|---|---|
| Correct-action accuracy | TODO | TODO | TODO |
| Policy compliance | TODO | TODO | TODO |
| Stale-memory leakage | TODO | TODO | TODO |
| Recall@5 | N/A | TODO | — |
| Injected tokens | TODO | TODO | — |

## Architecture

See `docs/architecture.mmd` and `docs/architecture.png`.

## Security model

- No remediation executes without human approval.
- Credentials and sensitive patterns are redacted before any model call or database write.
- Proposals pass through an allowlist validator before simulated execution.
- Logs and external tool content are untrusted: embedded instructions cannot become preferences or policies.

## Deployment

See `docs/deployment.md` for Alibaba Cloud ECS + ApsaraDB RDS instructions.

## Reset

`POST /api/demo/reset` restores seeded fixtures without affecting other tenants.

## License

Apache-2.0
