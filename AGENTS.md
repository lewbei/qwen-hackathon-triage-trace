# AGENTS.md

## Project

TriageTrace is a temporal memory firewall for Qwen-powered incident-response agents.

The product must preserve these guarantees:

* useful experience accumulates across sessions;
* newer authoritative memories supersede stale memories;
* poisoned, superseded, expired, future-dated, and cross-tenant memories are not recalled;
* public callers cannot assign trusted provenance, authority, status, or tenant identity;
* simulation-screened outcomes are not described as execution-validated;
* judge-facing PASS/FAIL results are derived from actual system state.

## Repository

* `backend/` — FastAPI, memory lifecycle, agent logic, and tests
* `frontend/` — React judge-facing interface
* `deploy/` — Alibaba Cloud and deployment configuration
* `docs/` — architecture, benchmarks, threat model, and operational documentation

Follow any more specific `AGENTS.md` found inside the directory being modified.

## Commands

Backend tests:

```bash
pytest backend/tests
```

Frontend build:

```bash
cd frontend
npm ci
npm run build
```

Docker build:

```bash
docker compose build
```

## Engineering approach

Fix the shared invariant, not only the visible symptom, example, or demo output.

For non-trivial correctness, security, or architecture work:

1. Identify the violated invariant and root cause.
2. Consider at least two materially different solutions.
3. Compare their correctness, generality, complexity, and failure modes.
4. Prefer the smallest solution that removes the failure class.
5. Add a regression test that fails before the fix.

Do not:

* hardcode successful verdicts or recalled memory IDs;
* bypass production workflows in demos;
* duplicate shared lifecycle or decision logic;
* trust client-controlled authority fields;
* silently convert correctness failures into successful fallbacks;
* claim tests, CI, benchmarks, deployment, or smoke tests succeeded without evidence;
* strengthen documentation claims beyond what the implementation proves.

`if/else` is allowed. Prefer clear local conditionals over unnecessary abstractions. Centralize repeated domain decisions and avoid brittle substring logic for semantic correctness.

## Definition of done

A behavioral task is complete only when:

* the root invariant is enforced;
* all relevant entry points use the fix;
* regression coverage exists;
* affected tests pass;
* relevant builds pass;
* documentation matches the implementation;
* remaining limitations are reported honestly.

A happy-path demo alone is not sufficient.
