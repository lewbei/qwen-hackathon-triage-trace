from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.memory import pack_memories, search_memories
from backend.app.models import MemoryRecord
from backend.app.qwen import qwen
from backend.app.schemas import ActionProposal, Alert, Mode, RunEvent
from backend.app.tools import TOOLS, dispatch


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
    model: str | None = None,
    token_usage: dict[str, int] | None = None,
    latency_ms: float | None = None,
) -> RunEvent:
    return RunEvent(
        event_type=event_type,
        timestamp=_now(),
        trace_id=run_id,
        payload=payload,
        model=model,
        token_usage=token_usage,
        latency_ms=latency_ms,
    )


async def run_incident(
    db: AsyncSession,
    alert: Alert,
    mode: Mode,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    events: list[RunEvent] = []
    events.append(_event(run_id, "run.started", {"mode": mode, "alert": alert.model_dump()}))

    recalled_ids: list[str] = []
    memory_context = ""
    if mode == Mode.memory:
        # Use a naive embedding (empty) for now; replaced by real embedding when QWEN_API_KEY set.
        query_embedding = None
        try:
            embeddings = await qwen.embed([f"{alert.service} {alert.symptom} {alert.context}"])
            query_embedding = embeddings[0]
            events.append(_event(run_id, "memory.embedded", {"dim": len(query_embedding)}))
        except Exception as e:
            events.append(_event(run_id, "memory.embed_failed", {"error": str(e)}))

        memories = await search_memories(
            db,
            tenant=alert.tenant,
            scope=alert.service,
            query_embedding=query_embedding,
            limit=30,
        )
        events.append(_event(run_id, "memory.retrieved", {"count": len(memories)}))

        packed, omitted, rejected = pack_memories(memories, budget=settings.memory_token_budget)
        recalled_ids = [str(m.id) for m in packed]
        events.append(_event(
            run_id,
            "memory.packed",
            {
                "packed": recalled_ids,
                "omitted": [str(m.id) for m in omitted],
                "rejected": [str(m.id) for m in rejected],
                "budget": settings.memory_token_budget,
            },
        ))

        memory_lines = []
        for m in packed:
            tag = "POLICY" if m.type == "policy" else "PREFERENCE" if m.type == "preference" else m.type.upper()
            memory_lines.append(f"[{tag}] {m.content}")
        memory_context = "\n".join(memory_lines)

    system = (
        "You are TriageTrace, an incident-response assistant. "
        "Use the provided tools to inspect the service. "
        "Then propose exactly one safe remediation action. "
        "Do not execute changes. Always set approval_required to true. "
        "If the service is unknown or evidence is insufficient, set insufficient_evidence to true."
    )
    if memory_context:
        system += f"\n\nRelevant validated experience (under {settings.memory_token_budget} tokens):\n{memory_context}"

    user = f"Incident on service '{alert.service}': {alert.symptom}. Context: {alert.context}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    first = await qwen.chat(messages=messages, tools=TOOLS, tool_choice="auto")
    events.append(_event(
        run_id,
        "model.reasoning",
        {"tool_calls": first.get("tool_calls")},
        model=first.get("model"),
        token_usage=first.get("token_usage"),
        latency_ms=first.get("latency_ms"),
    ))

    tool_results: list[dict[str, Any]] = []
    for tc in first.get("tool_calls", []):
        name = tc["function"]["name"]
        arguments = json.loads(tc["function"]["arguments"])
        result = dispatch(name, arguments)
        tool_results.append({"tool": name, "arguments": arguments, "result": result})
    events.append(_event(run_id, "tools.called", {"results": tool_results}))

    messages.append({"role": "assistant", "content": None, "tool_calls": [
        {
            "id": tc["id"],
            "type": "function",
            "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
        }
        for tc in first.get("tool_calls", [])
    ]})
    for i, tr in enumerate(tool_results):
        messages.append({
            "role": "tool",
            "tool_call_id": first["tool_calls"][i]["id"],
            "content": json.dumps(tr["result"]),
        })

    messages.append({
        "role": "system",
        "content": "Now produce a final JSON ActionProposal using the schema provided. If insufficient evidence, set action to 'none' and insufficient_evidence to true.",
    })

    second = await qwen.chat(messages=messages, temperature=0.0, max_tokens=2048)
    events.append(_event(
        run_id,
        "model.proposal",
        {"content": second.get("content")},
        model=second.get("model"),
        token_usage=second.get("token_usage"),
        latency_ms=second.get("latency_ms"),
    ))

    proposal_text = second.get("content") or "{}"
    if proposal_text.startswith("```"):
        proposal_text = proposal_text.split("```", 2)[-2] if proposal_text.count("```") >= 2 else proposal_text.strip("`")
    try:
        parsed = json.loads(proposal_text)
    except json.JSONDecodeError:
        start = proposal_text.find("{")
        end = proposal_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = json.loads(proposal_text[start : end + 1])
        else:
            parsed = {
                "action": "none",
                "service": alert.service,
                "evidence": "Model returned unparseable output.",
                "risk": "low",
                "approval_required": True,
                "insufficient_evidence": True,
            }

    if parsed.get("insufficient_evidence"):
        parsed["action"] = "none"
        parsed["status"] = "insufficient_evidence"
    else:
        parsed["status"] = "pending"

    proposal = ActionProposal(**parsed)
    proposal.recalled_memory_ids = recalled_ids

    return {
        "id": run_id,
        "tenant": alert.tenant,
        "mode": mode,
        "alert": alert,
        "events": events,
        "proposal": proposal,
    }
