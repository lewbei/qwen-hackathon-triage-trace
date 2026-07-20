from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Header, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent import run_incident
from backend.app.config import settings
from backend.app.decisions import apply_operator_decision
from backend.app.demo import (
    _scenario_tenant,
    run_accumulation_demo,
    run_winning_scenario,
)
from backend.app.demo_scenarios import get_scenario, get_scenarios, seed_demo_scenario
from backend.app.memory import create_memory, get_memory_lineage
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


class _DemoRateLimiter:
    """Simple per-IP sliding-window rate limiter for the public demo endpoints.

    This is intentionally lightweight: the demo runs in a single container and
    the limit only has to stop accidental abuse during judging. A proper deployment
    should put a reverse proxy or WAF in front.
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        import time

        now = time.time()
        timestamps = [t for t in self._hits.get(ip, []) if now - t < self._window]
        if len(timestamps) >= self._max:
            self._hits[ip] = timestamps
            return False
        timestamps.append(now)
        self._hits[ip] = timestamps
        return True


_write_limiter = _DemoRateLimiter(max_requests=5, window_seconds=60)
_read_limiter = _DemoRateLimiter(max_requests=60, window_seconds=60)
_EPHEMERAL_DEMO_COOKIE_SECRET = secrets.token_bytes(32)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Use the last proxy-supplied hop defensively. The production nginx
        # configuration replaces caller-controlled forwarding headers entirely.
        return forwarded.split(",")[-1].strip()
    return request.client.host or "unknown"


def _is_authenticated(secret: str) -> bool:
    """A request is authenticated when a demo secret is configured and matches."""
    return bool(settings.demo_secret and secret == settings.demo_secret)


def _demo_cookie_secret() -> bytes:
    """Return the signing key used for public demo tenant cookies.

    Alibaba deployment always configures ``DEMO_SECRET``. Development without a
    configured secret uses a process-local key, which is still non-forgeable but
    intentionally invalidates cookies after a restart.
    """
    if settings.demo_secret:
        return settings.demo_secret.encode("utf-8")
    return _EPHEMERAL_DEMO_COOKIE_SECRET


def _valid_demo_tenant(tenant: str) -> bool:
    if not tenant.startswith("demo-"):
        return False
    try:
        UUID(tenant.removeprefix("demo-"))
    except ValueError:
        return False
    return True


def _encode_demo_tenant_cookie(tenant: str) -> str:
    if not _valid_demo_tenant(tenant):
        raise ValueError("Invalid demo tenant")
    signature = hmac.new(_demo_cookie_secret(), tenant.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{tenant}.{signature}"


def _decode_demo_tenant_cookie(cookie: str | None) -> str | None:
    if not cookie:
        return None
    tenant, separator, supplied_signature = cookie.rpartition(".")
    if not separator or not _valid_demo_tenant(tenant):
        return None
    expected_signature = hmac.new(
        _demo_cookie_secret(), tenant.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        return None
    return tenant


def _set_demo_tenant_cookie(response: Response, tenant: str) -> None:
    response.set_cookie(
        key="demo_tenant",
        value=_encode_demo_tenant_cookie(tenant),
        httponly=True,
        samesite="lax",
        path="/",
    )


def _public_tenant(request: Request, response: Response) -> str:
    """Return the signed per-browser demo tenant, replacing forged cookies."""
    tenant = _decode_demo_tenant_cookie(request.cookies.get("demo_tenant"))
    if tenant:
        return tenant
    tenant = _scenario_tenant()
    _set_demo_tenant_cookie(response, tenant)
    return tenant


def _read_demo_tenant(request: Request) -> str | None:
    return _decode_demo_tenant_cookie(request.cookies.get("demo_tenant"))


def _resolve_tenant(requested: str, secret: str, request: Request, response: Response) -> str:
    """Authenticated callers may access any tenant; public callers are bound to their demo cookie."""
    if _is_authenticated(secret):
        return requested
    return _public_tenant(request, response)


async def _cleanup_demo_tenant(tenant: str) -> None:
    """Remove the transient demo tenant after the response has been sent."""
    from backend.app.models import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
        await session.execute(delete(RunRecord).where(RunRecord.tenant == tenant))
        await session.commit()


@app.get("/health")
@app.get("/api/health")
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
async def start_run(
    alert: Alert,
    request: Request,
    response: Response,
    mode: Mode = Mode.stateless,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> RunOut:
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    alert.tenant = _resolve_tenant(alert.tenant, x_demo_secret, request, response)
    run = await run_incident(db, alert, mode)
    run_error = run.get("error")
    proposal = run.get("proposal")
    status = "pending"
    if proposal and proposal.status == "invalid":
        status = "invalid"
        run_error = run_error or proposal.error
    elif run_error:
        status = "error"
    record = RunRecord(
        id=UUID(run["id"]),
        tenant=run["tenant"],
        mode=run["mode"],
        alert=run["alert"].model_dump(),
        events=_serialize_events(run["events"]),
        proposal=proposal.model_dump() if proposal else None,
        status=status,
    )
    db.add(record)
    await db.commit()
    return RunOut(
        id=record.id,
        tenant=record.tenant,
        mode=record.mode,
        alert=run["alert"],
        events=run["events"],
        proposal=proposal,
        status=record.status,
        error=run_error,
    )


async def _stream_run(alert: Alert, mode: Mode):
    queue: asyncio.Queue[Any | None] = asyncio.Queue(maxsize=64)
    final_result: dict[str, Any] | None = None

    async def worker() -> None:
        nonlocal final_result
        async with AsyncSessionLocal() as db:
            try:
                final_result = await run_incident(db, alert, mode, event_queue=queue)
                run_error = final_result.get("error")
                proposal = final_result.get("proposal")
                status = "pending"
                if proposal and proposal.status == "invalid":
                    status = "invalid"
                    run_error = run_error or proposal.error
                elif run_error:
                    status = "error"
                record = RunRecord(
                    id=UUID(final_result["id"]),
                    tenant=final_result["tenant"],
                    mode=final_result["mode"],
                    alert=final_result["alert"].model_dump(),
                    events=_serialize_events(final_result["events"]),
                    proposal=proposal.model_dump() if proposal else None,
                    status=status,
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
async def stream_run(
    alert: Alert,
    request: Request,
    mode: Mode = Mode.stateless,
    x_demo_secret: str = Header(default=""),
) -> StreamingResponse:
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    demo_tenant = _read_demo_tenant(request)
    if not demo_tenant:
        demo_tenant = _scenario_tenant()
    if _is_authenticated(x_demo_secret):
        resolved_tenant = alert.tenant
    else:
        resolved_tenant = demo_tenant
    alert.tenant = resolved_tenant
    stream = StreamingResponse(_stream_run(alert, mode), media_type="text/event-stream")
    _set_demo_tenant_cookie(stream, demo_tenant)
    return stream


@app.get("/api/agent/runs/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> RunOut:
    record = await db.get(RunRecord, UUID(run_id))
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    demo_tenant = _read_demo_tenant(request)
    if record.tenant != demo_tenant and not _is_authenticated(x_demo_secret):
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
async def get_events(
    run_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> dict[str, Any]:
    record = await db.get(RunRecord, UUID(run_id))
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    demo_tenant = _read_demo_tenant(request)
    if record.tenant != demo_tenant and not _is_authenticated(x_demo_secret):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "status": record.status, "events": record.events}


@app.post("/api/proposals/{run_id}/decision")
async def decide(
    run_id: str,
    decision: DecisionIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> dict[str, Any]:
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    record = await db.get(RunRecord, UUID(run_id))
    if not record or not record.proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    demo_tenant = _read_demo_tenant(request)
    if record.tenant != demo_tenant and not _is_authenticated(x_demo_secret):
        raise HTTPException(status_code=404, detail="Proposal not found")
    if record.status in ("error", "invalid"):
        raise HTTPException(status_code=400, detail=f"Cannot decide on a run with status {record.status}")
    return await apply_operator_decision(db, record, decision.approved, decision.feedback)


@app.get("/api/skills")
async def get_skills() -> dict[str, Any]:
    return {"skills": list_skills()}


@app.post("/api/skills/{name}/invoke")
async def invoke_skill_endpoint(
    name: str,
    arguments: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> dict[str, Any]:
    # Skill invocation can write memories with arbitrary provenance; restrict it to
    # demo administrators who provide the configured secret or to a rate-limited
    # public surface for read-only evidence tools.
    is_admin = _is_authenticated(x_demo_secret)
    if not is_admin:
        if name in {"inspect_metrics", "list_recent_deployments", "read_current_runbook"}:
            # Read-only evidence tools are safe for public demo use but still rate-limited.
            if not _read_limiter.allow(_client_ip(request)):
                raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
        elif name == "search_approved_memories":
            # Public callers may only search their own demo tenant memories.
            if not _read_limiter.allow(_client_ip(request)):
                raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
            demo_tenant = _read_demo_tenant(request)
            if not demo_tenant:
                raise HTTPException(status_code=403, detail="Demo tenant not initialized")
            arguments = {**arguments, "tenant": demo_tenant}
        else:
            raise HTTPException(status_code=403, detail="Skill invocation restricted. Provide a demo secret or use the public demo endpoints.")
    try:
        result = await invoke_skill(db, name, arguments)
        return {"skill": name, "result": result}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/memories")
async def list_memories(
    request: Request,
    response: Response,
    tenant: str = settings.default_tenant,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> list[MemoryRecordSchema]:
    tenant = _resolve_tenant(tenant, x_demo_secret, request, response)
    result = await db.execute(
        select(MemoryRecord).where(MemoryRecord.tenant == tenant).order_by(MemoryRecord.source_timestamp.desc()).limit(100)
    )
    rows = result.scalars().all()
    return [MemoryRecordSchema.model_validate(r) for r in rows]


@app.post("/api/memories")
async def add_untrusted_memory(
    tenant: str,
    provenance: str,
    type: str,
    scope: str,
    subject: str,
    predicate: str,
    content: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> MemoryRecordSchema:
    """Accept an externally submitted memory. Trusted provenance values are never
    accepted from the client; they are overridden to external/observation so the
    memory firewall always screens the content.
    """
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    tenant = _resolve_tenant(tenant, x_demo_secret, request, response)
    # Trusted provenance values may only be set by internal server workflows
    # (operator approval, simulated sandbox execution, etc.). A public caller cannot
    # bypass the poison gate by claiming trusted origin.
    trusted_provenances = {"operator", "approved_execution", "runbook", "simulation"}
    if provenance in trusted_provenances or type in ("procedure", "policy"):
        # Force the submission into the untrusted external observation path.
        provenance = "external"
        type = "observation"
    if provenance not in ("external", "model", "log", "tool"):
        provenance = "external"
    if type not in ("observation", "episode", "fact"):
        type = "observation"
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
async def memory_lineage(
    memory_id: str,
    request: Request,
    response: Response,
    tenant: str = settings.default_tenant,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> list[MemoryRecordSchema]:
    tenant = _resolve_tenant(tenant, x_demo_secret, request, response)
    try:
        mem_id = UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid memory id")
    lineage = await get_memory_lineage(db, tenant, mem_id)
    if not lineage:
        raise HTTPException(status_code=404, detail="Memory not found")
    return [MemoryRecordSchema.model_validate(r) for r in lineage]


@app.delete("/api/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    x_demo_secret: str = Header(default=""),
) -> dict[str, str]:
    """Mark a memory as deleted. Always requires the configured demo secret."""
    if not settings.demo_secret or x_demo_secret != settings.demo_secret:
        raise HTTPException(status_code=403, detail="invalid demo secret")
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
    # Reset is a destructive operation and always requires the configured demo secret.
    if not settings.demo_secret or x_demo_secret != settings.demo_secret:
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
async def winning_scenario(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run a controlled scenario demonstrating temporal supersession, poison quarantine,
    and memory-based incident response. Not for production use.

    Each call runs in an isolated tenant to avoid cross-user interference.
    Rate-limited per IP and the transient tenant is cleaned up after the response.
    """
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    tenant = _scenario_tenant()
    try:
        result = await run_winning_scenario(db, tenant=tenant)
    except Exception:
        await _cleanup_demo_tenant(tenant)
        raise
    background_tasks.add_task(_cleanup_demo_tenant, tenant)
    return result


@app.post("/api/demo/accumulation")
async def accumulation(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Run a controlled multi-session accumulation scenario.

    Simulates prior approved sessions (old procedure, newer procedure, poison attempt)
    and then runs a fresh incident to show that memory recalls only the current safe
    procedure. Not for production use.
    """
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many demo requests. Please wait a minute.")
    tenant = _scenario_tenant()
    try:
        result = await run_accumulation_demo(db, tenant=tenant)
    except Exception:
        await _cleanup_demo_tenant(tenant)
        raise
    background_tasks.add_task(_cleanup_demo_tenant, tenant)
    return result


@app.get("/api/demo/scenarios")
async def list_demo_scenarios() -> list[dict[str, Any]]:
    """Return the server-owned demo scenario catalog."""
    return get_scenarios()


@app.post("/api/demo/setup/{scenario_id}")
async def setup_demo_scenario(
    scenario_id: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Seed the caller's demo tenant with the requested scenario history.

    Each public visitor gets an isolated demo tenant via a cookie, so one visitor
    cannot reset another's demo state. The endpoint is rate-limited per IP.
    """
    try:
        get_scenario(scenario_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Scenario not found")
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many setup requests. Please wait a minute.")
    tenant = _public_tenant(request, response)
    return await seed_demo_scenario(db, scenario_id, tenant=tenant)


@app.post("/api/demo/setup")
async def setup_production_demo(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Seed the caller's demo tenant with the default cart-service scenario.

    Each public visitor gets an isolated demo tenant via a cookie, so one visitor
    cannot reset another's demo state. The endpoint is rate-limited per IP.
    """
    if not _write_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many setup requests. Please wait a minute.")
    tenant = _public_tenant(request, response)
    return await seed_demo_scenario(db, "cart-redis-latency", tenant=tenant)
