from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent import run_incident
from backend.app.config import settings
from backend.app.memory import create_memory
from backend.app.models import MemoryRecord, RunRecord, get_db
from backend.app.schemas import ActionProposal, Alert, DecisionIn, MemoryRecord as MemoryRecordSchema, Mode, RunOut

app = FastAPI(title="TriageTrace", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _serialize_events(events: list) -> list[dict[str, Any]]:
    out = []
    for e in events:
        if hasattr(e, "model_dump"):
            out.append(_json_safe(e.model_dump()))
        elif isinstance(e, dict):
            out.append(_json_safe(e))
        else:
            out.append(_json_safe(dict(e)))
    return out


@app.post("/api/agent/runs")
async def start_run(alert: Alert, mode: Mode = Mode.stateless, db: AsyncSession = Depends(get_db)) -> RunOut:
    run = await run_incident(db, alert, mode)
    record = RunRecord(
        id=UUID(run["id"]),
        tenant=run["tenant"],
        mode=run["mode"],
        alert=run["alert"].model_dump(),
        events=_serialize_events(run["events"]),
        proposal=run["proposal"].model_dump() if run.get("proposal") else None,
        status="pending",
    )
    db.add(record)
    await db.commit()
    return RunOut(
        id=record.id,
        tenant=record.tenant,
        mode=record.mode,
        alert=run["alert"],
        events=run["events"],
        proposal=run["proposal"],
        status=record.status,
    )


@app.get("/api/agent/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)) -> RunOut:
    record = await db.get(RunRecord, UUID(run_id))
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunOut(
        id=record.id,
        tenant=record.tenant,
        mode=record.mode,
        alert=Alert(**record.alert),
        events=[RunEvent(**e) for e in record.events],
        proposal=ActionProposal(**record.proposal) if record.proposal else None,
        status=record.status,
        decision=record.decision,
    )


@app.get("/api/agent/runs/{run_id}/events")
async def get_events(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    record = await db.get(RunRecord, UUID(run_id))
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "status": record.status, "events": record.events}


@app.post("/api/proposals/{run_id}/decision")
async def decide(run_id: str, decision: DecisionIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    record = await db.get(RunRecord, UUID(run_id))
    if not record or not record.proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    proposal = ActionProposal(**record.proposal)
    alert = Alert(**record.alert)
    record.decision = {"approved": decision.approved, "feedback": decision.feedback}
    if decision.approved:
        proposal.status = "approved"
        record.status = "approved"
        memory = await create_memory(
            db,
            tenant=record.tenant,
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
        record.proposal = proposal.model_dump()
        await db.commit()
        return {"run_id": run_id, "approved": True, "feedback": decision.feedback, "status": "approved", "memory_id": str(memory.id)}
    proposal.status = "rejected"
    record.status = "rejected"
    await create_memory(
        db,
        tenant=record.tenant,
        provenance="operator",
        type="preference",
        scope=proposal.service,
        subject=alert.symptom,
        predicate="avoid",
        content=f"Rejected action: {proposal.action}. Operator feedback: {decision.feedback}",
        source_authority=100,
        auto_embed=True,
    )
    record.proposal = proposal.model_dump()
    await db.commit()
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
    import json
    from pathlib import Path
    latest = Path(__file__).parent.parent.parent / "evaluations" / "latest.json"
    if latest.exists():
        return json.loads(latest.read_text())
    return {"status": "not yet generated"}


async def _seed_fixture(db: AsyncSession, tenant: str, service: str) -> int:
    import json
    from pathlib import Path
    base = Path(__file__).parent.parent / "fixtures" / service
    if not base.exists():
        return 0
    count = 0
    for file in base.glob("*.json"):
        data = json.loads(file.read_text())
        text = json.dumps(data, default=str)
        await create_memory(
            db,
            tenant=tenant,
            provenance="runbook" if "runbook" in file.name else "tool",
            type="observation",
            scope=service,
            subject=file.stem,
            predicate="snapshot",
            content=f"[{service}] {file.stem}: {text[:500]}",
            auto_embed=True,
        )
        count += 1
    return count


@app.post("/api/demo/reset")
async def demo_reset(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    await db.execute(delete(MemoryRecord))
    await db.execute(delete(RunRecord))
    await db.commit()
    seeded = 0
    for service in ["cart-service", "payment-service"]:
        seeded += await _seed_fixture(db, settings.default_tenant, service)
    await db.commit()
    return {"status": "reset", "seeded": seeded}
