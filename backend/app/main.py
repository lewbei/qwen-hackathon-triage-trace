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
from backend.app.schemas import Alert, DecisionIn, MemoryRecord as MemoryRecordSchema, Mode, RunOut

app = FastAPI(title="TriageTrace", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/agent/runs")
async def start_run(alert: Alert, mode: Mode = Mode.stateless, db: AsyncSession = Depends(get_db)) -> RunOut:
    run = await run_incident(db, alert, mode)
    return RunOut(**run)


@app.get("/api/agent/runs/{run_id}/events")
async def get_events(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Event persistence not implemented")


@app.post("/api/proposals/{run_id}/decision")
async def decide(run_id: str, decision: DecisionIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return {"run_id": run_id, "approved": decision.approved, "feedback": decision.feedback, "status": "not_implemented"}


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
