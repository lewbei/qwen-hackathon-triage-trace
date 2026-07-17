from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.memory import create_memory, pack_memories, search_memories


@pytest.mark.asyncio
async def test_memory_supersession(db_session: AsyncSession):
    old = await create_memory(
        db_session,
        tenant="default",
        provenance="runbook",
        type="preference",
        scope="cart-service",
        subject="safety",
        predicate="action",
        content="Old preference: restart cart-service pods",
    )
    assert old.status == "active"
    new = await create_memory(
        db_session,
        tenant="default",
        provenance="runbook",
        type="preference",
        scope="cart-service",
        subject="safety",
        predicate="action",
        content="New preference: scale Redis and restart workers",
    )
    assert new.status == "active"
    await db_session.refresh(old)
    assert old.status == "superseded"


@pytest.mark.asyncio
async def test_token_packing_respects_budget(db_session: AsyncSession):
    for i in range(10):
        await create_memory(
            db_session,
            tenant="default",
            provenance="operator",
            type="preference",
            scope="cart-service",
            subject="safety",
            predicate=f"rule-{i}",
            content=f"Do not restart the database. Rule {i} " + "x " * 100,
        )
    memories = await search_memories(db_session, tenant="default", scope="cart-service")
    packed, omitted, rejected = pack_memories(memories, budget=200)
    assert len(packed) < len(memories)
    assert all(m.status == "active" for m in packed)


@pytest.mark.asyncio
async def test_out_of_order_memory_is_quarantined(db_session: AsyncSession):
    """A delayed older record must not supersede the current active memory."""
    now = datetime.now(timezone.utc)
    current = await create_memory(
        db_session,
        tenant="default",
        provenance="simulation",
        type="procedure",
        scope="notification-service",
        subject="queue_backlog",
        predicate="remediation",
        content="Current: scale workers and requeue messages.",
        source_authority=80,
        source_timestamp=now,
        embedding=[0.0] * 1536,
        auto_embed=False,
    )
    assert current.status == "active"

    delayed_old = await create_memory(
        db_session,
        tenant="default",
        provenance="simulation",
        type="procedure",
        scope="notification-service",
        subject="queue_backlog",
        predicate="remediation",
        content="Stale: restart all pods immediately.",
        source_authority=80,
        source_timestamp=now - timedelta(days=7),
        embedding=[1.0] * 1536,
        auto_embed=False,
    )
    assert delayed_old.status == "quarantined"
    assert "stale" in (delayed_old.meta.get("quarantine_reason") or "")
    await db_session.refresh(current)
    assert current.status == "active"
