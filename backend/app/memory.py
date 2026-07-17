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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    import math
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# MemoryGate: pre-write validation to quarantine poisoned or policy-violating memories.
POISON_PATTERNS = [
    r"ignore all (policies|instructions|previous)",
    r"bypass all checks",
    r"refund immediately",
    r"restart the database",
    r"delete user preferences",
    r"disregard (?:all|the) (?:policies|instructions|safety|rules)",
    r"you must (?:ignore|bypass|override)",
]


def _detect_poison(content: str) -> str | None:
    import re
    lower = content.lower()
    for pattern in POISON_PATTERNS:
        if re.search(pattern, lower):
            return f"poison pattern matched: {pattern}"
    return None


def _policy_contradiction(content: str, policy_content: str) -> str | None:
    """Naive contradiction check: policy says 'Never X' and memory says 'X'."""
    import re
    p = policy_content.lower()
    m = content.lower()
    # Find 'never' phrases in policy and check if the forbidden action appears in memory.
    for match in re.finditer(r"never\s+(.+?)(?:\.|$|\s+first|\s+without)", p):
        forbidden = match.group(1).strip().lower()
        # Extract the core verb phrase (first 3-5 words) to avoid over-matching.
        core = " ".join(forbidden.split()[:4])
        if core and core in m:
            return f"contradicts policy: {match.group(0).strip()}"
    return None


async def _memory_gate(session: AsyncSession, record: MemoryRecord) -> bool:
    """Validate a memory before it is persisted. Returns True if accepted."""
    # Trusted sources bypass heuristic checks, but still respect active policies.
    trusted = record.provenance in ("operator", "approved_execution")

    if not trusted:
        poison = _detect_poison(record.content)
        if poison:
            record.status = "quarantined"
            record.meta["quarantine_reason"] = poison
            return False

    # Check against active policies in the same scope/subject.
    result = await session.execute(
        select(MemoryRecord).where(
            MemoryRecord.tenant == record.tenant,
            MemoryRecord.scope == record.scope,
            MemoryRecord.subject == record.subject,
            MemoryRecord.type == "policy",
            MemoryRecord.status == "active",
        )
    )
    for policy in result.scalars():
        contradiction = _policy_contradiction(record.content, policy.content)
        if contradiction:
            record.status = "quarantined"
            record.meta["quarantine_reason"] = f"policy contradiction: {contradiction}"
            return False

    return True


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
    auto_embed: bool = False,
) -> MemoryRecord:
    content = redact(content)
    if embedding is None and auto_embed:
        embedding = (await qwen.embed([content], dimensions=1536))[0]
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
    # MemoryGate: quarantine poisoned or policy-violating memories.
    gate_ok = await _memory_gate(session, record)

    if not gate_ok:
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record

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
    elif record.status == "candidate" and type == "procedure" and authority >= 80:
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


def _min_max_normalize(values: list[float]) -> list[float]:
    min_v = min(values) if values else 0
    max_v = max(values) if values else 0
    if max_v == min_v:
        return [0.0 for _ in values]
    return [(v - min_v) / (max_v - min_v) for v in values]


def _compute_utility(memory: MemoryRecord, relevance: float, now: datetime) -> float:
    # Normalized components.
    importance = max(0.0, min(1.0, memory.importance))
    trust = max(0.0, min(1.0, memory.source_authority / 100))
    age_days = (now - memory.source_timestamp).total_seconds() / 86400 if memory.source_timestamp else 0
    freshness = max(0.0, 1.0 - (age_days / 30.0))
    utility = max(0.0, min(1.0, memory.utility))
    # Weights: 45% relevance, 20% importance, 15% trust, 10% freshness, 10% utility.
    return 0.45 * relevance + 0.20 * importance + 0.15 * trust + 0.10 * freshness + 0.10 * utility


def _apply_mmr(
    memories: list[MemoryRecord],
    utilities: dict[uuid.UUID, float],
    query_embedding: list[float] | None,
    k: int = 10,
    lambda_param: float = 0.75,
) -> list[MemoryRecord]:
    """Maximal Marginal Relevance: balance relevance with diversity among selected memories."""
    selected: list[MemoryRecord] = []
    remaining = list(memories)
    while remaining and len(selected) < k:
        best = None
        best_score = -1.0
        for m in remaining:
            relevance = utilities.get(m.id, 0.0)
            if query_embedding and m.embedding:
                query_sim = _cosine_similarity(query_embedding, m.embedding)
            else:
                query_sim = relevance
            if selected:
                max_sim = max(_cosine_similarity(m.embedding or [], s.embedding or []) for s in selected)
            else:
                max_sim = 0.0
            score = lambda_param * query_sim - (1 - lambda_param) * max_sim
            # Combine utility-aware score with raw MMR score.
            score = 0.6 * relevance + 0.4 * score
            if score > best_score:
                best_score = score
                best = m
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
    return selected


def _pack_memories(
    memories: list[MemoryRecord],
    budget: int = 800,
) -> tuple[list[MemoryRecord], list[MemoryRecord], int]:
    """Greedy pack: policies/preferences first, then others, within token budget."""
    eligible = [m for m in memories if m.status == "active"]
    eligible.sort(key=lambda m: (0 if m.type in ("policy", "preference") else 1, -m.utility))
    packed: list[MemoryRecord] = []
    omitted: list[MemoryRecord] = []
    used = _tokens("Relevant memories:\n")
    for m in eligible:
        cost = _tokens(m.content) + 4
        if used + cost > budget:
            omitted.append(m)
            continue
        used += cost
        m.access_count += 1
        m.last_accessed = _now()
        packed.append(m)
    return packed, omitted, used


def _rerank_fallback(
    memories: list[MemoryRecord],
    query_embedding: list[float] | None,
) -> dict[uuid.UUID, float]:
    """Fallback reranking using cosine similarity; replaces qwen3-rerank when unavailable or to save quota."""
    scores: dict[uuid.UUID, float] = {}
    for m in memories:
        if query_embedding and m.embedding:
            scores[m.id] = _cosine_similarity(query_embedding, m.embedding)
        else:
            scores[m.id] = 0.0
    return scores


async def retrieve_and_pack(
    session: AsyncSession,
    tenant: str,
    scope: str,
    query_text: str,
    query_embedding: list[float] | None = None,
    budget: int = 800,
    candidate_limit: int = 30,
    rerank_limit: int = 10,
    mmr_k: int = 10,
    mmr_lambda: float = 0.75,
) -> tuple[list[MemoryRecord], list[MemoryRecord], list[MemoryRecord], dict[str, Any]]:
    """Full retrieval lifecycle: vector candidates, rerank, score, MMR, pack."""
    now = _now()
    candidates = await search_memories(session, tenant, scope, query_embedding, limit=candidate_limit)

    # Rerank fallback (qwen3-rerank can be wired here later).
    relevance = _rerank_fallback(candidates, query_embedding)

    # Compute utility per candidate.
    utilities = {m.id: _compute_utility(m, relevance.get(m.id, 0.0), now) for m in candidates}
    for m in candidates:
        m.utility = utilities[m.id]

    # MMR selection over top reranked candidates.
    reranked = sorted(candidates, key=lambda m: utilities[m.id], reverse=True)[:rerank_limit]
    selected = _apply_mmr(reranked, utilities, query_embedding, k=mmr_k, lambda_param=mmr_lambda)

    # Pack into token budget.
    packed, omitted, used_tokens = _pack_memories(selected, budget=budget)

    # Rejected status memories are reported for audit but not packed.
    rejected = [m for m in candidates if m.status in ("quarantined", "superseded", "expired")]

    metadata = {
        "candidates": len(candidates),
        "selected": len(selected),
        "packed": len(packed),
        "omitted": len(omitted),
        "rejected": len(rejected),
        "used_tokens": used_tokens,
        "budget": budget,
    }
    return packed, omitted, rejected, metadata


def pack_memories(
    memories: list[MemoryRecord],
    budget: int = 800,
) -> tuple[list[MemoryRecord], list[MemoryRecord], list[MemoryRecord]]:
    """Legacy simple pack for tests and direct callers."""
    packed, omitted, _ = _pack_memories(memories, budget=budget)
    rejected = [m for m in memories if m.status in ("quarantined", "superseded", "expired")]
    return packed, omitted, rejected


async def get_memory_lineage(session: AsyncSession, tenant: str, memory_id: uuid.UUID) -> list[MemoryRecord]:
    """Return the chain of memories from the requested record back through supersedes_id."""
    lineage: list[MemoryRecord] = []
    current_id = memory_id
    visited: set[uuid.UUID] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        record = await session.get(MemoryRecord, current_id)
        if not record or record.tenant != tenant:
            break
        lineage.append(record)
        current_id = record.supersedes_id
    return lineage
