import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import demo as demo_module
from backend.app import main as main_module
from backend.app.config import settings
from backend.app.main import app
from backend.app.models import MemoryRecord, RunRecord
from backend.app.schemas import ActionProposal, Alert

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _configure_demo_secret(monkeypatch):
    monkeypatch.setattr(settings, "demo_secret", SECRET)

    class _NoopLimiter:
        def allow(self, ip: str) -> bool:
            return True

    monkeypatch.setattr(main_module, "_demo_limiter", _NoopLimiter())


def _memory_json(tenant: str, content: str = "benign observation"):
    return {
        "tenant": tenant,
        "provenance": "operator",
        "type": "procedure",
        "scope": "test-service",
        "subject": "test-subject",
        "predicate": "test-predicate",
        "content": content,
    }


async def _post_memory(client: AsyncClient, secret: str = "", params: dict | None = None):
    return await client.post(
        "/api/memories",
        params=params,
        headers={"x-demo-secret": secret} if secret else {},
    )


@pytest.mark.asyncio
async def test_public_memory_write_rejects_trusted_provenance(db_session):
    """Public memory submissions cannot claim trusted provenance or procedure type."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _post_memory(client, params=_memory_json("attacker-tenant"))
    assert response.status_code == 200
    data = response.json()
    assert data["tenant"] == settings.default_tenant
    assert data["provenance"] == "external"
    assert data["type"] == "observation"


@pytest.mark.asyncio
async def test_memory_tenant_isolation_requires_secret(db_session):
    """Listing another tenant's memories requires the demo secret."""
    attacker = "attacker-tenant"
    mem = MemoryRecord(
        id=uuid.uuid4(),
        tenant=attacker,
        provenance="external",
        type="observation",
        scope="test-service",
        subject="test-subject",
        predicate="test-predicate",
        content="attacker observation",
        embedding=None,
        token_count=10,
        status="active",
    )
    db_session.add(mem)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        no_secret = await client.get("/api/memories", params={"tenant": attacker})
        with_secret = await client.get(
            "/api/memories",
            params={"tenant": attacker},
            headers={"x-demo-secret": SECRET},
        )

    assert no_secret.status_code == 200
    assert no_secret.json() == []
    assert with_secret.status_code == 200
    assert with_secret.json()[0]["id"] == str(mem.id)


@pytest.mark.asyncio
async def test_delete_memory_requires_secret(db_session):
    """DELETE /api/memories/{id} must reject unauthenticated callers."""
    mem = MemoryRecord(
        id=uuid.uuid4(),
        tenant=settings.default_tenant,
        provenance="external",
        type="observation",
        scope="test-service",
        subject="test-subject",
        predicate="test-predicate",
        content="observation",
        embedding=None,
        token_count=10,
        status="active",
    )
    db_session.add(mem)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        no_secret = await client.delete(f"/api/memories/{mem.id}")
        with_secret = await client.delete(
            f"/api/memories/{mem.id}", headers={"x-demo-secret": SECRET}
        )

    assert no_secret.status_code == 403
    assert with_secret.status_code == 200
    assert with_secret.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_run_and_decision_endpoints_enforce_tenant_isolation(db_session, monkeypatch):
    """Run lookups and decisions only cross the default tenant without the secret."""
    attacker = "attacker-tenant"
    run = RunRecord(
        id=uuid.uuid4(),
        tenant=attacker,
        mode="memory",
        alert=Alert(
            tenant=attacker,
            service="test-service",
            symptom="test",
            context="test",
            severity="warning",
        ).model_dump(),
        proposal=ActionProposal(
            action="Scale workers",
            service="test-service",
            evidence="test",
            risk="low",
            approval_required=True,
            status="pending",
            recalled_memory_ids=[],
            insufficient_evidence=False,
        ).model_dump(),
        events=[],
        status="pending",
    )
    db_session.add(run)
    await db_session.commit()

    monkeypatch.setattr(
        main_module,
        "run_incident",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        main_module,
        "apply_operator_decision",
        AsyncMock(return_value={"status": "simulated_safe", "memory_id": str(uuid.uuid4())}),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_no = await client.get(f"/api/agent/runs/{run.id}")
        get_yes = await client.get(
            f"/api/agent/runs/{run.id}", headers={"x-demo-secret": SECRET}
        )
        events_no = await client.get(f"/api/agent/runs/{run.id}/events")
        events_yes = await client.get(
            f"/api/agent/runs/{run.id}/events", headers={"x-demo-secret": SECRET}
        )
        decision_no = await client.post(
            f"/api/proposals/{run.id}/decision", json={"approved": True, "feedback": "ok"}
        )
        decision_yes = await client.post(
            f"/api/proposals/{run.id}/decision",
            json={"approved": True, "feedback": "ok"},
            headers={"x-demo-secret": SECRET},
        )

    assert get_no.status_code == 404
    assert get_yes.status_code == 200
    assert events_no.status_code == 404
    assert events_yes.status_code == 200
    assert decision_no.status_code == 404
    assert decision_yes.status_code == 200


@pytest.mark.asyncio
async def test_demo_endpoints_run_and_cleanup(db_session, monkeypatch):
    """The demo endpoints must execute the scenario and return a verdict."""
    from backend.app.demo import Mode as DemoMode

    async def _fake_run_incident(session, alert, mode):
        from backend.app.models import MemoryRecord
        from backend.app.schemas import Alert as AlertSchema
        from sqlalchemy import select

        result = await session.execute(
            select(MemoryRecord).where(
                MemoryRecord.tenant == alert.tenant,
                MemoryRecord.scope == alert.service,
            )
        )
        rows = list(result.scalars().all())
        if alert.service == "notification-service":
            new_mem = next(
                (m for m in rows if "requeue failed messages" in m.content.lower()), None
            )
            action = "Scale notification workers and requeue failed messages"
        else:
            new_mem = next(
                (m for m in rows if "restart the cart workers" in m.content.lower()), None
            )
            action = "Scale Redis and restart cart workers"
        recalled = [str(new_mem.id)] if new_mem and mode == DemoMode.memory else []
        return {
            "id": f"demo-run-{mode.value}",
            "tenant": alert.tenant,
            "mode": mode,
            "alert": alert,
            "events": [],
            "proposal": ActionProposal(
                action=action if mode == DemoMode.memory else "do nothing",
                service=alert.service,
                evidence="test",
                risk="low",
                approval_required=True,
                status="pending",
                recalled_memory_ids=recalled,
                insufficient_evidence=False,
            ),
        }

    monkeypatch.setattr(demo_module, "run_incident", _fake_run_incident)
    monkeypatch.setattr(
        demo_module.qwen,
        "embed",
        AsyncMock(return_value=[[0.0] * 1536, [1.0] * 1536, [-1.0] * 1536]),
    )

    cleanup_tenants: list[str] = []

    async def tracked_cleanup(tenant: str) -> None:
        """Record the cleanup call and perform deletion with the test session."""
        cleanup_tenants.append(tenant)
        from sqlalchemy import delete

        await db_session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
        await db_session.execute(delete(RunRecord).where(RunRecord.tenant == tenant))
        await db_session.commit()

    monkeypatch.setattr(main_module, "_cleanup_demo_tenant", tracked_cleanup)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        winning = await client.post("/api/demo/winning-scenario")
        accumulation = await client.post("/api/demo/accumulation")

    assert winning.status_code == 200
    assert accumulation.status_code == 200
    for response in (winning, accumulation):
        data = response.json()
        assert "demo_passed" in data
        assert data["summary"]["memory_firewall_passed"] is True
        assert data["summary"]["agent_behaviour_passed"] is True
        assert data["summary"]["demo_passed"] is True
        tenant = data["tenant"]
        assert tenant in cleanup_tenants
        assert await _count_tenant_records(db_session, tenant) == (0, 0)


async def _count_tenant_records(db_session, tenant: str) -> tuple[int, int]:
    from sqlalchemy import func, select

    mem_count = await db_session.scalar(
        select(func.count()).select_from(MemoryRecord).where(MemoryRecord.tenant == tenant)
    )
    run_count = await db_session.scalar(
        select(func.count()).select_from(RunRecord).where(RunRecord.tenant == tenant)
    )
    return mem_count or 0, run_count or 0


@pytest.mark.asyncio
async def test_demo_exception_cleanup(db_session, monkeypatch):
    """If the scenario raises, the endpoint must still delete the transient tenant."""
    scenario_tenant = "test-exception-cleanup"
    cleanup_called: list[str] = []

    async def tracked_cleanup(tenant: str) -> None:
        cleanup_called.append(tenant)
        from sqlalchemy import delete

        await db_session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
        await db_session.execute(delete(RunRecord).where(RunRecord.tenant == tenant))
        await db_session.commit()

    monkeypatch.setattr(main_module, "_scenario_tenant", lambda: scenario_tenant)
    monkeypatch.setattr(main_module, "_cleanup_demo_tenant", tracked_cleanup)

    async def failing_scenario(session, tenant):
        # Insert records before raising so we can verify cleanup actually removes them.
        from datetime import datetime, timezone
        from uuid import uuid4

        session.add(
            MemoryRecord(
                id=uuid4(),
                tenant=tenant,
                provenance="external",
                type="observation",
                scope="test-service",
                subject="test-subject",
                predicate="test-predicate",
                content="transient record",
                embedding=None,
                token_count=10,
                status="active",
            )
        )
        session.add(
            RunRecord(
                id=uuid4(),
                tenant=tenant,
                mode="memory",
                alert={},
                proposal=None,
                events=[],
                status="running",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        raise RuntimeError("simulated scenario failure")

    monkeypatch.setattr(main_module, "run_winning_scenario", failing_scenario)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError):
            await client.post("/api/demo/winning-scenario")

    assert cleanup_called == [scenario_tenant]
    assert await _count_tenant_records(db_session, scenario_tenant) == (0, 0)
