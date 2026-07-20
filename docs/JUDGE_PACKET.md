# TriageTrace — Judge Packet

## One-line pitch

TriageTrace is a **temporal memory firewall** for Qwen-powered incident-response agents. It lets on-call teams accumulate approved remediation procedures across sessions, automatically supersede stale runbooks, and quarantine poisoned or policy-violating instructions before the agent ever proposes them.

## What the demo shows

The production triage UI (`/`) loads a realistic `cart-service` incident:

- Redis latency has spiked, checkout failures are above 40/min, and a recent deployment was rolled back.
- The memory firewall already contains:
  - an older operator-approved remediation (later superseded);
  - a newer, safer remediation that matches the current signal pattern;
  - an untrusted external instruction that was quarantined.
- The operator clicks **Run triage**. The Qwen agent:
  1. recalls only the safe, current memory;
  2. calls the evidence tools (metrics, runbook, deployments);
  3. proposes a remediation.
- The operator approves, the deterministic simulator predicts the outcome, and a new `simulated_safe` memory is written to the firewall.

## Live endpoints

- Local UI: `http://localhost`
- Health / readiness: `GET /api/health`
- Skills surface: `GET /api/skills`
- Agent run (memory mode): `POST /api/agent/runs?mode=memory`
- Operator decision: `POST /api/proposals/{run_id}/decision`
- Memory list: `GET /api/memories?tenant=default`

## Quick start for judges

```bash
# 1. Start the stack
docker compose -f docker-compose.prod.yml up -d --build

# 2. Verify
curl -s http://localhost/api/health

# 3. Open http://localhost in a browser and click Initialize demo, then Run triage, then Approve.
```

## Key files to review

- `backend/app/agent.py` — Qwen function-calling agent.
- `backend/app/memory.py` — memory lifecycle, conflict table, retrieval, and packing.
- `backend/app/decisions.py` — operator approval + deterministic simulation gate.
- `backend/app/demo.py` — `seed_cart_service_history` sets up the default tenant.
- `docs/architecture.mmd` — system diagram.
- `docs/screenshots/` — captured demo walkthrough.
- `backend/tests/` — unit and integration tests.

## Track fit

- **Primary track:** Track 1 — MemoryAgent.
- **Core safety claims:** human approval gate, memory-driven reasoning, poison quarantine, temporal supersession.
