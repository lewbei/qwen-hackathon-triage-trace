from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.memory import create_memory, retrieve_and_pack
from backend.app.models import MemoryRecord
from backend.app.schemas import Alert
from backend.app.tools import TOOLS as EVIDENCE_TOOLS, dispatch as dispatch_evidence


SKILL_REGISTRY: list[dict[str, Any]] = [
    *EVIDENCE_TOOLS,
    {
        "type": "function",
        "function": {
            "name": "search_validated_memories",
            "description": (
                "Search the tenant's memory firewall for active, validated incident "
                "memories (procedures, preferences, policies) relevant to a service "
                "and incident description. Returns packed memories plus metadata "
                "about omitted and rejected memories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant": {"type": "string"},
                    "service": {"type": "string"},
                    "symptom": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["tenant", "service", "symptom"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_validated_lesson",
            "description": (
                "Store a validated incident lesson in the memory firewall. Only operator "
                "or approved_execution provenance is trusted enough to become active; "
                "untrusted sources are quarantined by MemoryGate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tenant": {"type": "string"},
                    "provenance": {"type": "string"},
                    "type": {"type": "string", "enum": ["observation", "procedure", "preference", "policy", "fact"]},
                    "scope": {"type": "string"},
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["tenant", "provenance", "type", "scope", "subject", "predicate", "content"],
            },
        },
    },
]


def list_skills() -> list[dict[str, Any]]:
    """Return all available custom skills in OpenAI tool format."""
    return SKILL_REGISTRY


async def invoke_skill(
    session: AsyncSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a skill by name. Memory skills require an async DB session."""
    if name in {"inspect_metrics", "list_recent_deployments", "read_current_runbook"}:
        return dispatch_evidence(name, arguments)

    if name == "search_validated_memories":
        tenant = arguments["tenant"]
        service = arguments["service"]
        symptom = arguments.get("symptom", "")
        context = arguments.get("context", "")
        query = f"{service} {symptom} {context}".strip()
        packed, omitted, rejected, meta = await retrieve_and_pack(
            session,
            tenant=tenant,
            scope=service,
            query_text=query,
            budget=800,
        )
        return {
            "packed": [
                {
                    "id": str(m.id),
                    "type": m.type,
                    "scope": m.scope,
                    "subject": m.subject,
                    "content": m.content,
                    "status": m.status,
                    "source_authority": m.source_authority,
                }
                for m in packed
            ],
            "omitted": [str(m.id) for m in omitted],
            "rejected": [str(m.id) for m in rejected],
            "meta": meta,
        }

    if name == "remember_validated_lesson":
        record = await create_memory(
            session,
            tenant=arguments["tenant"],
            provenance=arguments["provenance"],
            type=arguments["type"],
            scope=arguments["scope"],
            subject=arguments["subject"],
            predicate=arguments["predicate"],
            content=arguments["content"],
            auto_embed=True,
        )
        return {
            "id": str(record.id),
            "type": record.type,
            "scope": record.scope,
            "subject": record.subject,
            "content": record.content,
            "status": record.status,
            "quarantine_reason": record.meta.get("quarantine_reason"),
        }

    raise ValueError(f"Unknown skill: {name}")
