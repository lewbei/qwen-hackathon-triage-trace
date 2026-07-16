from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, JSON, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.config import settings


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryRecord(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant: Mapped[str] = mapped_column(String(64), index=True, default="default")
    session: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[str] = mapped_column(String(128))
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    source_authority: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(128), index=True)
    subject: Mapped[str] = mapped_column(String(256))
    predicate: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    utility: Mapped[float] = mapped_column(Float, default=0.0)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="candidate")
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


engine = create_async_engine(settings.database_url, echo=settings.app_env == "development")
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
