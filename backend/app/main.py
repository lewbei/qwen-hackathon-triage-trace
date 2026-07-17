from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent import run_incident
from backend.app.config import settings
from backend.app.memory import create_memory
from backend.app.models import MemoryRecord, get_db
from backend.app.schemas import ActionProposal, Alert, DecisionIn, MemoryRecord as MemoryRecordSchema, Mode, RunOut

app = FastAPI(title="TriageTrace", version="0.1.0")

# In-memory run store for the approval gate; replaced by persistent run storage later.
RUN_STORE: dict[str, dict] = {}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/agent/runs")
async def start_run(alert: Alert, mode: Mode = Mode.stateless, db: AsyncSession = Depends(get_db)) -> RunOut:
    run = await run_incident(db, alert, mode)
    RUN_STORE[run["id"]] = run
    return RunOut(**run)


@app.get("/api/agent/runs/{run_id}/events")
async def get_events(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Event persistence not implemented")


@app.post("/api/proposals/{run_id}/decision")
async def decide(run_id: str, decision: DecisionIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run = RUN_STORE.get(run_id)
    if not run or not run.get("proposal"):
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal: ActionProposal = run["proposal"]
    alert: Alert = run["alert"]
    if decision.approved:
        proposal.status = "approved"
        memory = await create_memory(
            db,
            tenant=run["tenant"],
            provenance="approved_execution",
            type="procedure",
            scope=proposal.service,
            subject=alert.symptom,
            predicate="remediation",
            content=f"Approved procedure: {proposal.action}. Evidence: {proposal.evidence}. Operator feedback: {decision.feedback}",
            source_authority=80,
            auto_embed=True,
        )
        memory.status = "active"
        await db.commit()
        return {"run_id": run_id, "approved": True, "feedback": decision.feedback, "status": "approved", "memory_id": str(memory.id)}
    proposal.status = "rejected"
    await create_memory(
        db,
        tenant=run["tenant"],
        provenance="operator",
        type="preference",
        scope=proposal.service,
        subject=alert.symptom,
        predicate="avoid",
        content=f"Rejected action: {proposal.action}. Operator feedback: {decision.feedback}",
        source_authority=100,
        auto_embed=True,
    )
    return {"run_id": run_id, "approved": False, "feedback": decision.feedback, "status": "rejected"}


@app.get("/api/memories")
async def list_memories(tenant: str = settings.default_tenant, db: AsyncSession = Depends(get_db)) -> list[MemoryRecordSchema]:
    result = await db.execute(
        select(MemoryRecord).where(MemoryRecord.tenant == tenant).order_by(MemoryRecord.source_timestamp.desc()).limit(100)
    )
    rows = result.scalars().all()
    return [MemoryRecordSchema.model_validate(r) for r in rows]


@app.post("/api/memories")
async def add_memory(
    tenant: str,
    provenance: str,
    type: str,
    scope: str,
    subject: str,
    predicate: str,
    content: str,
    db: AsyncSession = Depends(get_db),
) -> MemoryRecordSchema:
    record = await create_memory(
        db,
        tenant=tenant,
        provenance=provenance,
        type=type,
        scope=scope,
        subject=subject,
        predicate=predicate,
        content=content,
        auto_embed=True,
    )
    return MemoryRecordSchema.model_validate(record)


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    record = await db.get(MemoryRecord, UUID(memory_id))
    if not record:
        raise HTTPException(status_code=404, detail="Memory not found")
    record.status = "deleted"
    await db.commit()
    return {"status": "deleted", "id": memory_id}


@app.get("/api/evaluations/latest")
async def latest_evaluations() -> dict[str, Any]:
    return {"status": "not yet generated"}


@app.post("/api/demo/reset")
async def demo_reset(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    await db.execute(delete(MemoryRecord))
    await db.commit()
    return {"status": "reset"}
