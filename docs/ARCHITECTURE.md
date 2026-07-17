# TriageTrace Architecture

TriageTrace is a **temporal memory firewall for incident-response agents**. It wraps Qwen Cloud models with a persistent, validated, adversarially-hardened memory layer.

## Components

| Component | File | Responsibility |
|---|---|---|
| **React Dashboard** | `frontend/src/App.tsx` | Operator UI: trigger incidents, approve/reject proposals, inspect memory lens, view evaluation dashboard. |
| **FastAPI Orchestrator** | `backend/app/main.py` | HTTP API, SSE streaming, skill registry, run lifecycle, memory CRUD. |
| **Agent** | `backend/app/agent.py` | Runs the two-stage Qwen3.7-plus reasoning loop: tool reasoning → final proposal. Emits events for real-time streaming. |
| **Memory Firewall** | `backend/app/memory.py` | Vector retrieval, rerank fallback, MMR diversity, utility scoring, token-budget packing, supersession, quarantine (MemoryGate). |
| **Custom Skills** | `backend/app/skills.py` | OpenAI-compatible tool definitions for evidence tools plus memory search/lesson skills. |
| **Qwen Client** | `backend/app/qwen.py` | Thin OpenAI-compatible client over Qwen Cloud for chat and `text-embedding-v4` vectors. |
| **Evaluation Harness** | `backend/app/eval.py` + `backend/scripts/evaluate.py` | Deterministic mock and live Qwen evaluation with adversarial metrics. |
| **Database** | `backend/app/models.py` | SQLAlchemy + pgvector `MemoryRecord` and `RunRecord` tables, Alembic migrations. |

## Request flow

1. `POST /api/agent/runs` (or `/api/agent/runs/stream`) receives an `Alert`.
2. `agent.py` embeds the alert and calls `memory.retrieve_and_pack`.
3. `memory.py` searches the vector index, reranks, scores utility, applies MMR, and packs under `MEMORY_TOKEN_BUDGET`.
4. The agent invokes evidence tools (`inspect_metrics`, `read_current_runbook`, etc.).
5. Qwen3.7-plus produces an `ActionProposal` with `approval_required: true`.
6. `POST /api/proposals/{id}/decision` records operator approval/rejection and writes a validated memory.
7. All events are persisted in `RunRecord` and can be streamed via SSE.

## Memory lifecycle

```text
candidate
   │
   ├─ MemoryGate checks ──► quarantined (poison / contradiction / duplicate / lower authority)
   │
   ├─ policy / preference ──► active
   ├─ procedure, authority >=80 ──► active
   └─ other types ──► active

active
   │
   ├─ newer equal-or-higher authority ──► superseded
   ├─ TTL expires ──► expired
   └─ operator delete ──► deleted
```

## Key design decisions

- **No autonomous execution:** every remediation is a proposal awaiting operator approval.
- **Provenance-first trust:** `operator` and `approved_execution` provenance bypass heuristic poison checks because they are validated human or gated outputs.
- **Policy packing priority:** active `policy` memories are packed before `preference`/`procedure` memories so the model sees governance constraints first.
- **Token budget enforcement:** memory context is strictly bounded, with omitted and rejected memories reported for audit.

## Scalability notes

- The backend is stateless; horizontal scaling only requires sharing the PostgreSQL + pgvector database.
- Skill registry and tool dispatch are pure functions with no global state.
- SSE streaming is per-connection and does not require a message broker.
