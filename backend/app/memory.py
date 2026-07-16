from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import tiktoken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.models import MemoryRecord
from backend.app.qwen import qwen

ENCODER = tiktoken.get_encoding("cl100k_base")

DEFAULT_TTLS = {
    "observation": timedelta(hours=24),
    "episode": timedelta(days=30),
    "procedure": timedelta(days=14),
    "fact": timedelta(days=7),
    "preference": None,
    "policy": None,
}

AUTHORITY = {
    "operator": 100,
    "runbook": 90,
    "approved_execution": 80,
    "tool": 50,
    "model": 40,
    "log": 10,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(text: str) -> int:
    return len(ENCODER.encode(text))


def _compute_expires_at(type: str, valid_from: datetime) -> datetime | None:
    ttl = DEFAULT_TTLS.get(type)
    if ttl is None:
        return None
    return valid_from + ttl


def redact(text: str) -> str:
    """Remove obvious credential patterns before model or DB."""
    import re
    text = re.sub(r"(password|secret|token|api[-_]?key)\s*[:=]\s*\S+", r"\1=***REDACTED***", text, flags=re.I)
    return text


async def create_memory(
    session: AsyncSession,
    tenant: str,
    provenance: str,
    type: str,
    scope: str,
    subject: str,
    predicate: str,
    content: str,
    source_authority: int | None = None,
    valid_from: datetime | None = None,
    embedding: list[float] | None = None,
    meta: dict[str, Any] | None = None,
) -> MemoryRecord:
    content = redact(content)
    authority = source_authority if source_authority is not None else AUTHORITY.get(provenance, 0)
    valid = valid_from or _now()
    expires = _compute_expires_at(type, valid)
    token_count = _tokens(content)
    record = MemoryRecord(
        id=uuid.uuid4(),
        tenant=tenant,
        provenance=provenance,
        source_timestamp=_now(),
        source_authority=authority,
        type=type,
        scope=scope,
        subject=subject,
        predicate=predicate,
        content=content,
        embedding=embedding,
        token_count=token_count,
        importance=0.5,
        confidence=0.5,
        utility=0.0,
        valid_from=valid,
        expires_at=expires,
        status="candidate",
        meta=meta or {},
    )
    # Lifecycle: compare with active existing memories of same scope/subject.
    existing = await session.execute(
        select(MemoryRecord).where(
            MemoryRecord.tenant == tenant,
            MemoryRecord.scope == scope,
            MemoryRecord.subject == subject,
            MemoryRecord.predicate == predicate,
            MemoryRecord.status.in_(["active", "candidate"]),
        )
    )
    for old in existing.scalars():
        if old.content == record.content:
            # duplicate
            record.status = "quarantined"
            record.meta["quarantine_reason"] = "duplicate"
            break
        if old.source_authority > record.source_authority:
            record.status = "quarantined"
            record.meta["quarantine_reason"] = "lower authority than existing"
            break
        if old.source_authority <= record.source_authority:
            old.status = "superseded"
            record.supersedes_id = old.id
            record.status = "active"
    if record.status == "candidate" and type in ("preference", "policy"):
        record.status = "active"
    elif record.status == "candidate" and type != "procedure":
        record.status = "active"
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def search_memories(
    session: AsyncSession,
    tenant: str,
    scope: str,
    query_embedding: list[float] | None = None,
    limit: int = 30,
) -> list[MemoryRecord]:
    now = _now()
    stmt = select(MemoryRecord).where(
        MemoryRecord.tenant == tenant,
        MemoryRecord.scope == scope,
        MemoryRecord.status == "active",
        (MemoryRecord.expires_at.is_(None)) | (MemoryRecord.expires_at > now),
        MemoryRecord.valid_from <= now,
    )
    if query_embedding is not None:
        stmt = stmt.order_by(MemoryRecord.embedding.cosine_distance(query_embedding)).limit(limit)
    else:
        stmt = stmt.order_by(MemoryRecord.source_timestamp.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def promote_procedure(session: AsyncSession, memory_id: uuid.UUID) -> None:
    record = await session.get(MemoryRecord, memory_id)
    if record and record.type == "procedure" and record.status == "candidate":
        record.status = "active"
        await session.commit()


def pack_memories(
    memories: list[MemoryRecord],
    budget: int = 800,
) -> tuple[list[MemoryRecord], list[MemoryRecord], list[MemoryRecord]]:
    """Pack memories into token budget. Returns (packed, omitted, rejected)."""
    # Simple greedy pack: safety/preferences first, then highest utility.
    rejected = [m for m in memories if m.status in ("quarantined", "superseded", "expired")]
    eligible = [m for m in memories if m.status == "active"]
    eligible.sort(key=lambda m: (0 if m.type in ("policy", "preference") else 1, -(m.utility + m.importance)))
    packed: list[MemoryRecord] = []
    omitted: list[MemoryRecord] = []
    used = 0
    header = "Relevant memories:\n"
    used += _tokens(header)
    for m in eligible:
        cost = _tokens(m.content) + 4
        if used + cost > budget:
            omitted.append(m)
            continue
        used += cost
        m.access_count += 1
        m.last_accessed = _now()
        packed.append(m)
    return packed, omitted, rejected
