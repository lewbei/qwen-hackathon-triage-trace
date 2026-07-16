from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Mode(str, Enum):
    stateless = "stateless"
    memory = "memory"


class Alert(BaseModel):
    tenant: str = "default"
    service: str
    symptom: str
    severity: str = "warning"
    context: str = ""


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None


class ActionProposal(BaseModel):
    action: str
    service: str
    evidence: str
    risk: str = "low"
    approval_required: bool = True
    status: str = "pending"
    recalled_memory_ids: list[str] = []


class RunEvent(BaseModel):
    event_type: str
    timestamp: datetime
    trace_id: str
    payload: dict[str, Any]
    model: str | None = None
    token_usage: dict[str, int] | None = None
    latency_ms: float | None = None


class RunOut(BaseModel):
    id: UUID
    tenant: str
    mode: Mode
    alert: Alert
    events: list[RunEvent]
    proposal: ActionProposal | None = None


class DecisionIn(BaseModel):
    approved: bool
    feedback: str = ""


class MemoryRecord(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    tenant: str
    session: str | None = None
    provenance: str
    source_timestamp: datetime
    source_authority: int
    type: str
    scope: str
    subject: str
    predicate: str
    content: str
    embedding: list[float] | None = None
    token_count: int
    importance: float
    confidence: float
    utility: float
    valid_from: datetime
    expires_at: datetime | None = None
    supersedes_id: UUID | None = None
    status: str
    access_count: int = 0
    last_accessed: datetime | None = None
    meta: dict[str, Any] = {}
