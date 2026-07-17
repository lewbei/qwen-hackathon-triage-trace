from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.memory import create_memory, get_memory_lineage, pack_memories, search_memories


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_memory(
    session: AsyncSession,
    content: str,
    source_authority: int,
    source_timestamp: datetime,
    scope: str = "test-service",
    subject: str = "test-subject",
    predicate: str = "test-predicate",
    tenant: str = "default",
    embedding: list[float] | None = None,
) -> Any:
    return await create_memory(
        session,
        tenant=tenant,
        provenance="simulation",
        type="procedure",
        scope=scope,
        subject=subject,
        predicate=predicate,
        content=content,
        source_authority=source_authority,
        source_timestamp=source_timestamp,
        embedding=embedding or ([0.0] * 1536),
        auto_embed=False,
    )


@pytest.mark.asyncio
async def test_newer_equal_authority_supersedes_older(db_session: AsyncSession):
    """Case 1: old inserted first, newer inserted second; newer wins."""
    now = _now()
    old = await create_memory(
        db_session,
        tenant="default",
        provenance="simulation",
        type="procedure",
        scope="test-service",
        subject="test-subject",
        predicate="test-predicate",
        content="Old procedure",
        source_authority=80,
        source_timestamp=now - timedelta(days=7),
        embedding=[0.0] * 1536,
        auto_embed=False,
    )
    assert old.status == "active"

    new = await create_memory(
        db_session,
        tenant="default",
        provenance="simulation",
        type="procedure",
        scope="test-service",
        subject="test-subject",
        predicate="test-predicate",
        content="New procedure",
        source_authority=80,
        source_timestamp=now,
        embedding=[1.0] * 1536,
        auto_embed=False,
    )
    assert new.status == "active"
    assert str(new.supersedes_id) == str(old.id)
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
async def test_out_of_order_equal_authority_is_quarantined(db_session: AsyncSession):
    """Case 2: newer inserted first, older inserted second; older must lose."""
    now = _now()
    await _make_memory(db_session, "Current procedure", 80, now)
    stale = await _make_memory(db_session, "Stale procedure", 80, now - timedelta(days=7))
    assert stale.status == "quarantined"
    assert "stale or out-of-order" in (stale.meta.get("quarantine_reason") or "")


@pytest.mark.asyncio
async def test_equal_timestamp_equal_authority_is_quarantined(db_session: AsyncSession):
    """Case 3: two records with equal authority and equal timestamp cannot both be active."""
    now = _now()
    first = await _make_memory(db_session, "First procedure", 80, now)
    second = await _make_memory(db_session, "Second procedure", 80, now)
    assert first.status == "active"
    assert second.status == "quarantined"
    assert "stale or out-of-order" in (second.meta.get("quarantine_reason") or "")


@pytest.mark.asyncio
async def test_lower_authority_newer_is_quarantined(db_session: AsyncSession):
    """Case 4: authority dominates timestamp; lower authority never supersedes higher."""
    now = _now()
    strong = await _make_memory(db_session, "Strong procedure", 90, now - timedelta(days=7))
    weak = await _make_memory(db_session, "Weak newer procedure", 80, now)
    assert strong.status == "active"
    assert weak.status == "quarantined"
    assert "lower authority" in (weak.meta.get("quarantine_reason") or "")


@pytest.mark.asyncio
async def test_higher_authority_older_supersedes(db_session: AsyncSession):
    """Case 5: higher authority supersedes lower authority regardless of timestamp."""
    now = _now()
    weak = await _make_memory(db_session, "Weak current procedure", 80, now)
    strong = await _make_memory(db_session, "Strong older procedure", 90, now - timedelta(days=7))
    assert weak.status == "superseded"
    assert strong.status == "active"
    assert str(strong.supersedes_id) == str(weak.id)


@pytest.mark.asyncio
async def test_duplicate_content_is_quarantined(db_session: AsyncSession):
    """Case 6: duplicate content is quarantined, not made active."""
    now = _now()
    first = await _make_memory(db_session, "Same procedure", 80, now)
    duplicate = await _make_memory(db_session, "Same procedure", 80, now + timedelta(days=1))
    assert first.status == "active"
    assert duplicate.status == "quarantined"
    assert "duplicate" in (duplicate.meta.get("quarantine_reason") or "")


@pytest.mark.asyncio
async def test_retrieval_excludes_rejected_and_superseded(db_session: AsyncSession):
    """Case 7: search_memories must only return active/simulated_safe memories."""
    now = _now()
    old = await _make_memory(db_session, "Old procedure", 80, now - timedelta(days=7), embedding=[0.0] * 1536)
    new = await _make_memory(db_session, "New procedure", 80, now, embedding=[0.1] * 1536)
    stale = await _make_memory(db_session, "Stale procedure", 80, now - timedelta(days=14), embedding=[0.2] * 1536)

    await db_session.refresh(old)
    assert old.status == "superseded"
    assert new.status == "active"
    assert stale.status == "quarantined"

    results = await search_memories(
        db_session,
        tenant="default",
        scope="test-service",
        query_embedding=[0.0] * 1536,
    )
    assert len(results) == 1
    assert results[0].id == new.id


@pytest.mark.asyncio
async def test_lineage_follows_supersedes_chain(db_session: AsyncSession):
    """Case 8: lineage walks supersedes_id back from the current memory."""
    now = _now()
    oldest = await _make_memory(db_session, "Oldest procedure", 80, now - timedelta(days=14))
    middle = await _make_memory(db_session, "Middle procedure", 80, now - timedelta(days=7))
    current = await _make_memory(db_session, "Current procedure", 80, now)

    await db_session.refresh(oldest)
    await db_session.refresh(middle)
    await db_session.refresh(current)

    assert current.status == "active"
    assert middle.status == "superseded"
    assert oldest.status == "superseded"
    assert str(current.supersedes_id) == str(middle.id)
    assert str(middle.supersedes_id) == str(oldest.id)

    lineage = await get_memory_lineage(db_session, "default", current.id)
    lineage_ids = [m.id for m in lineage]
    assert lineage_ids == [current.id, middle.id, oldest.id]
