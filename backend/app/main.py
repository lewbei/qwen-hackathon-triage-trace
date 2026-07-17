from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent import run_incident
from backend.app.demo import run_accumulation_demo, run_winning_scenario
from backend.app.config import settings
from backend.app.memory import create_memory, get_memory_lineage
from backend.app.simulate import simulate_action
from backend.app.models import AsyncSessionLocal, MemoryRecord, RunRecord, get_db
from backend.app.schemas import (
    ActionProposal,
    Alert,
    DecisionIn,
    MemoryRecord as MemoryRecordSchema,
    Mode,
    RunEvent,
    RunOut,
)
from backend.app.skills import invoke_skill, list_skills

app = FastAPI(title="TriageTrace", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _json_safe(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return _json_safe(obj.model_dump())
    if hasattr(obj, "__dict__") and not isinstance(obj, (str, bytes, int, float, bool, type(None))):
        return _json_safe({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
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


async def _stream_run(alert: Alert, mode: Mode):
    queue: asyncio.Queue[Any | None] = asyncio.Queue(maxsize=64)
    final_result: dict[str, Any] | None = None

    async def worker() -> None:
        nonlocal final_result
        async with AsyncSessionLocal() as db:
            try:
                final_result = await run_incident(db, alert, mode, event_queue=queue)
                record = RunRecord(
                    id=UUID(final_result["id"]),
                    tenant=final_result["tenant"],
                    mode=final_result["mode"],
                    alert=final_result["alert"].model_dump(),
                    events=_serialize_events(final_result["events"]),
                    proposal=final_result["proposal"].model_dump() if final_result.get("proposal") else None,
                    status="pending",
                )
                db.add(record)
                await db.commit()
            except Exception as exc:
                await queue.put({"event_type": "run.error", "payload": {"error": str(exc)}})
            finally:
                await queue.put(None)

    task = asyncio.create_task(worker())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            data = json.dumps(_json_safe(event))
            yield f"data: {data}\n\n"
        if final_result:
            final = {"event_type": "run.result", "payload": final_result}
            yield f"data: {json.dumps(_json_safe(final))}\n\n"
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.post("/api/agent/runs/stream")
async def stream_run(alert: Alert, mode: Mode = Mode.stateless) -> StreamingResponse:
    return StreamingResponse(_stream_run(alert, mode), media_type="text/event-stream")


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

    # Try to use the metrics the agent actually observed during the run.
    observed_metrics: dict[str, Any] | None = None
    for ev in record.events or []:
        payload = ev.get("payload") or {}
        if ev.get("event_type") == "tools.called" and isinstance(payload, dict):
            for result in payload.get("results", []):
                if result.get("tool") == "inspect_metrics" and isinstance(result.get("result"), dict):
                    observed_metrics = result["result"]
                    break
            if observed_metrics:
                break

    outcome = simulate_action(proposal.service, proposal.action, observed_metrics)
    record.decision = {
        "approved": decision.approved,
        "feedback": decision.feedback,
        "outcome": outcome,
    }

    if decision.approved and outcome["improved"]:
        proposal.status = "validated"
        record.status = "validated"
        memory = await create_memory(
            db,
            tenant=record.tenant,
            provenance="validated_execution",
            type="procedure",
            scope=proposal.service,
            subject=alert.symptom,
            predicate="remediation",
            content=f"Validated procedure: {proposal.action}. Evidence: {proposal.evidence}. Simulated outcome: {outcome['reasoning']} (delta {outcome['delta']:+}). Operator feedback: {decision.feedback}",
            source_authority=90,
            auto_embed=True,
        )
        memory.status = "active"
        record.proposal = proposal.model_dump()
        await db.commit()
        return {
            "run_id": run_id,
            "approved": True,
            "validated": True,
            "feedback": decision.feedback,
            "status": "validated",
            "memory_id": str(memory.id),
            "outcome": outcome,
        }

    # If the operator approved but the simulated outcome does not improve, reject the
    # action and record a negative preference so the agent learns to avoid it.
    if decision.approved:
        proposal.status = "rejected_by_simulation"
        record.status = "rejected_by_simulation"
        await create_memory(
            db,
            tenant=record.tenant,
            provenance="failed_execution",
            type="preference",
            scope=proposal.service,
            subject=alert.symptom,
            predicate="avoid",
            content=f"Avoid action: {proposal.action}. Simulated outcome: {outcome['reasoning']} (delta {outcome['delta']:+}). Operator feedback: {decision.feedback}",
            source_authority=95,
            auto_embed=True,
        )
        record.proposal = proposal.model_dump()
        await db.commit()
        return {
            "run_id": run_id,
            "approved": False,
            "validated": False,
            "feedback": decision.feedback,
            "status": "rejected_by_simulation",
            "outcome": outcome,
        }

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
    return {
        "run_id": run_id,
        "approved": False,
        "validated": False,
        "feedback": decision.feedback,
        "status": "rejected",
        "outcome": outcome,
    }


@app.get("/api/skills")
async def get_skills() -> dict[str, Any]:
    return {"skills": list_skills()}


@app.post("/api/skills/{name}/invoke")
async def invoke_skill_endpoint(
    name: str,
    arguments: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await invoke_skill(db, name, arguments)
        return {"skill": name, "result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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


@app.get("/api/memories/{memory_id}/lineage")
async def memory_lineage(memory_id: str, tenant: str = settings.default_tenant, db: AsyncSession = Depends(get_db)) -> list[MemoryRecordSchema]:
    try:
        mem_id = UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid memory id")
    lineage = await get_memory_lineage(db, tenant, mem_id)
    if not lineage:
        raise HTTPException(status_code=404, detail="Memory not found")
    return [MemoryRecordSchema.model_validate(r) for r in lineage]


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
async def demo_reset(
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> dict[str, Any]:
    # In production, require the configured demo secret. In development/demo,
    # reset is allowed without a secret so the quickstart and hackathon demo work.
    if settings.app_env == "production" and (not settings.demo_secret or x_demo_secret != settings.demo_secret):
        raise HTTPException(status_code=403, detail="invalid demo secret")
    tenant = settings.default_tenant
    await db.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
    await db.execute(delete(RunRecord).where(RunRecord.tenant == tenant))
    await db.commit()
    seeded = 0
    for service in ["cart-service", "payment-service"]:
        seeded += await _seed_fixture(db, tenant, service)
    await db.commit()
    return {"status": "reset", "seeded": seeded}


@app.post("/api/demo/winning-scenario")
async def winning_scenario(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Run a controlled scenario demonstrating temporal supersession, poison quarantine,
    and memory-based incident response. Not for production use.

    Each call runs in an isolated tenant to avoid cross-user interference.
    """
    return await run_winning_scenario(db)


@app.post("/api/demo/accumulation")
async def accumulation(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Run a controlled multi-session accumulation scenario.

    Simulates prior approved sessions (old procedure, newer procedure, poison attempt)
    and then runs a fresh incident to show that memory recalls only the current safe
    procedure. Not for production use.
    """
    return await run_accumulation_demo(db)
