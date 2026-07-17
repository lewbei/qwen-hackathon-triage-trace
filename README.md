# TriageTrace — A Temporal Memory Firewall for Incident Agents

**Track 1: MemoryAgent** — Qwen Hackathon 2026

TriageTrace is a temporal-memory incident-response agent that learns from validated outcomes and refuses poisoned, contradictory, or obsolete memories. It uses Qwen Cloud (`qwen3.7-plus` for reasoning, `text-embedding-v4` for memory vectors, plus slots for `qwen3.6-flash` extraction and `qwen3-rerank`) to propose, refine, and audit remediations.

## Hosted Demo

- Live URL: `https://TODO-alibaba-ecs-url`
- Public repository: `https://github.com/TODO/triage-trace`

## Quickstart

```bash
cp .env.example .env
# Add your Qwen Cloud API key to .env (free-tier keys must use the dashscope-intl endpoint already set in .env.example)
docker compose up --build
```

- API: `http://localhost:8000`
- Dashboard: `http://localhost:5173`
- Health: `GET /health`

Run a stateless and memory incident:

```bash
curl -s -X POST "http://localhost:8000/api/agent/runs?mode=stateless" \
  -H "Content-Type: application/json" \
  -d '{"service":"cart-service","symptom":"High error rate and slow checkout","context":"Started after Redis latency spike"}'

# Approve a proposal (replace <run-id>):
curl -s -X POST "http://localhost:8000/api/proposals/<run-id>/decision" \
  -H "Content-Type: application/json" \
  -d '{"approved":true,"feedback":"operator confirmed"}'
```

## What it proves

1. A stateless Qwen agent inspects fixtures and proposes a remediation.
2. An operator approves or rejects it; the validated lesson becomes a durable `procedure` or `preference` memory with a vector embedding.
3. A later incident in the same scope triggers memory retrieval: vector candidates, reranking/fallback, MMR diversity scoring, utility weighting, and 800-token packing. Policies and preferences are packed first.
4. A newer validated procedure supersedes an older one; a memory that contradicts a higher-authority source is quarantined; a malicious instruction embedded in a log is not promoted.
5. `POST /api/demo/reset` reseeds fixture observations without touching other tenants.

## Benchmarks

Results are committed to `evaluations/latest.json` and rendered below. The latest committed run is a **full live Qwen evaluation on all 5 scenarios**:

| Metric | Stateless | Memory | Δ |
|---|---|---|---|
| Correct-action accuracy | 40% | 60% | +20% |
| Policy compliance | 100% | 100% | 0% |
| Avg latency | 33.1 s | 24.7 s | −8.4 s |
| Avg total tokens | 2,462 | 2,303 | −159 |
| Avg injected memory tokens | 0 | 20 | +20 |
| Recalled memory IDs | 0 | 4 / 5 | +4 |

Scenario highlights:

- `repeated-1`: stateless and memory both correct (runbook already contains the fix); memory recalls the validated procedure.
- `temporal-1`: stateless suggests updating the runbook; memory recalls the newer `runbook 2.0` procedure and returns `Apply runbook 2.0 steps`.
- `policy-1`: memory recalls the operator policy and stays policy-compliant, while the stateless baseline still tries to restart workers (not scale the connection pool).
- `poison-1`: both modes correctly return `none` / `insufficient_evidence` instead of executing the injected malicious instruction.
- `unknown-1`: both modes correctly decline (no fixtures).

Run the full deterministic harness (no Qwen quota used):

```bash
python backend/scripts/evaluate.py
```

Run the live Qwen evaluation:

```bash
python backend/scripts/evaluate.py --live
```

## Architecture

```
Incident alert
      |
      v
+--------------+      +-----------------+      +------------------+
|   Agent      |----->|  Evidence tools |      |   Qwen Cloud     |
|  (FastAPI)   |      |  (metrics,      |<---->| qwen3.7-plus     |
+--------------+      |  deployments,   |      | text-embedding-v4|
      |               |  runbooks)      |      | qwen3.6-flash    |
      v               +-----------------+      +------------------+
+--------------+
|  Memory      |  pgvector  +-------------------------------+
|  firewall:   |<---------->|  MemoryRecord lifecycle       |
|  vector      |            |  active/superseded/quarantine |
|  retrieve    |            |  MMR + utility + token pack   |
+--------------+            +-------------------------------+
```

Detailed architecture: `docs/architecture.mmd` and `docs/deployment.md`.

## Security model

- No remediation executes without human approval (`approval_required` is always true).
- Credentials and sensitive patterns are redacted before any model call or database write.
- Memories are typed (`observation`, `procedure`, `preference`, `policy`, `fact`) and expire based on TTLs; procedures need validation before promotion.
- Logs and external tool content are untrusted: embedded instructions cannot become preferences or policies.

## Deployment

See `docs/deployment.md` for Alibaba Cloud ECS + ApsaraDB RDS / pgvector instructions and the Docker Compose local fallback.

## Reset

`POST /api/demo/reset` restores seeded fixtures without affecting other tenants.

## License

Apache-2.0
