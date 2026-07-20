from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.memory import redact, retrieve_and_pack
from backend.app.models import MemoryRecord
from backend.app.qwen import qwen
from backend.app.schemas import ActionProposal, Alert, Mode, RunEvent
from backend.app.tools import TOOLS, dispatch


def _safe_parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON string, tolerating common LLM artifacts like markdown fences,
    trailing braces, or unbalanced wrapping. Falls back to extracting the first
    balanced `{...}` object.
    """
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown code fences if present.
        text = text.split("```", 2)[-2] if text.count("```") >= 2 else text.strip("`")
        text = text.strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if not text:
        return {}

    # First attempt: direct parse.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    except json.JSONDecodeError:
        pass

    # Second attempt: remove a trailing brace if the string ends with duplicated `}}`.
    if text.endswith("}}"):
        try:
            parsed = json.loads(text[:-1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    # Third attempt: extract the first balanced `{...}` object.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    # Last resort: return the raw text so the caller can decide.
    return {"_raw": text}


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


def _maybe_enqueue(queue: asyncio.Queue | None, event: RunEvent) -> None:
    if queue is not None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def run_incident(
    db: AsyncSession,
    alert: Alert,
    mode: Mode,
    event_queue: asyncio.Queue | None = None,
) -> dict[str, Any]:
    # Redact sensitive patterns from the incident context before any model call or persistence.
    alert = alert.model_copy(update={"context": redact(alert.context)})

    run_id = str(uuid.uuid4())
    events: list[RunEvent] = []

    def emit(event_type: str, payload: dict[str, Any], **kwargs: Any) -> RunEvent:
        ev = _event(run_id, event_type, payload, **kwargs)
        events.append(ev)
        _maybe_enqueue(event_queue, ev)
        return ev

    emit("run.started", {"mode": mode, "alert": alert.model_dump()})

    recalled_ids: list[str] = []
    memory_context = ""
    if mode == Mode.memory:
        # Use a naive embedding (empty) for now; replaced by real embedding when QWEN_API_KEY set.
        query_embedding = None
        try:
            embeddings = await qwen.embed([f"{alert.service} {alert.symptom} {alert.context}"], dimensions=1536)
            query_embedding = embeddings[0]
            emit("memory.embedded", {"dim": len(query_embedding)})
        except Exception as e:
            emit("memory.embed_failed", {"error": str(e)})

        packed, omitted, rejected, pack_meta = await retrieve_and_pack(
            db,
            tenant=alert.tenant,
            scope=alert.service,
            query_text=f"{alert.service} {alert.symptom} {alert.context}",
            query_embedding=query_embedding,
            budget=settings.memory_token_budget,
        )
        recalled_ids = [str(m.id) for m in packed]
        emit(
            "memory.packed",
            {
                "packed_ids": recalled_ids,
                "omitted_ids": [str(m.id) for m in omitted],
                "rejected_ids": [str(m.id) for m in rejected],
                "packed_count": len(packed),
                "omitted_count": len(omitted),
                "rejected_count": len(rejected),
                "candidates": pack_meta["candidates"],
                "selected": pack_meta["selected"],
                "used_tokens": pack_meta["used_tokens"],
                "budget": pack_meta["budget"],
            },
        )

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
        system += f"\n\nRelevant approved-and-simulated experience (under {settings.memory_token_budget} tokens):\n{memory_context}"

    user = f"Incident on service '{alert.service}': {alert.symptom}. Context: {alert.context}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    first = await qwen.chat(messages=messages, tools=TOOLS, tool_choice="auto")
    emit(
        "model.reasoning",
        {"tool_calls": first.get("tool_calls")},
        model=first.get("model"),
        token_usage=first.get("token_usage"),
        latency_ms=first.get("latency_ms"),
    )

    tool_results: list[dict[str, Any]] = []
    valid_tools = {t["function"]["name"] for t in TOOLS}
    for tc in first.get("tool_calls", []):
        func = tc.get("function") or {}
        name = func.get("name", "")
        if not name or name not in valid_tools:
            # Skip malformed or unknown tool calls instead of crashing the run.
            tool_results.append({"tool": name or "unknown", "arguments": {}, "result": {"error": f"unknown or empty tool: {name!r}"}})
            continue
        arguments = _safe_parse_json(func.get("arguments") or "{}")
        result = dispatch(name, arguments)
        tool_results.append({"tool": name, "arguments": arguments, "result": result})
    emit("tools.called", {"results": tool_results})

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
            "content": redact(json.dumps(tr["result"])),
        })

    messages.append({
        "role": "system",
        "content": (
            "Now produce a final JSON ActionProposal. "
            "You must include these exact fields: action (string), service (string), evidence (string), risk (low/medium/high), approval_required (boolean), insufficient_evidence (boolean). "
            "If insufficient evidence, set action to 'none' and insufficient_evidence to true."
        ),
    })

    second = await qwen.chat(messages=messages, temperature=0.0, max_tokens=2048)
    emit(
        "model.proposal",
        {"content": second.get("content")},
        model=second.get("model"),
        token_usage=second.get("token_usage"),
        latency_ms=second.get("latency_ms"),
    )

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

    # Ensure required fields exist with sensible defaults.
    parsed.setdefault("action", "none")
    parsed.setdefault("service", alert.service)
    parsed.setdefault("evidence", "No evidence provided.")
    parsed.setdefault("risk", "medium")
    parsed.setdefault("approval_required", True)
    parsed.setdefault("insufficient_evidence", False)

    if parsed.get("insufficient_evidence"):
        parsed["action"] = "none"
        parsed["status"] = "insufficient_evidence"
    else:
        parsed["status"] = "pending"

    proposal = ActionProposal(**parsed)
    proposal.recalled_memory_ids = recalled_ids

    result = {
        "id": run_id,
        "tenant": alert.tenant,
        "mode": mode,
        "alert": alert,
        "events": events,
        "proposal": proposal,
    }
    emit("run.completed", {"proposal": proposal.model_dump()})
    return result
