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
