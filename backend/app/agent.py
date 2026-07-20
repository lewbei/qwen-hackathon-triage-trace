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


def _validate_proposal_fields(parsed: dict[str, Any], default_service: str) -> tuple[bool, list[str]]:
    """Validate the parsed ActionProposal fields. Returns (valid, errors)."""
    errors: list[str] = []

    if not isinstance(parsed, dict) or parsed.get("_raw"):
        return False, ["proposal is not a valid JSON object"]

    action = str(parsed.get("action", "")).strip()
    insufficient = parsed.get("insufficient_evidence")
    if isinstance(insufficient, str):
        insufficient = insufficient.lower() in ("true", "yes", "1")
    elif not isinstance(insufficient, bool):
        insufficient = bool(insufficient)
    parsed["insufficient_evidence"] = insufficient

    if not action:
        errors.append("action is empty")
    elif action.lower() == "none":
        if not insufficient:
            errors.append("action is 'none' but insufficient_evidence is false")
    else:
        if insufficient:
            errors.append("insufficient_evidence is true but action is not 'none'")

    service = str(parsed.get("service", "")).strip()
    if not service:
        errors.append("service is empty")
    else:
        parsed["service"] = service

    evidence = str(parsed.get("evidence", "")).strip()
    if not evidence:
        errors.append("evidence is empty")
    else:
        parsed["evidence"] = evidence

    risk = str(parsed.get("risk", "")).strip().lower()
    if risk not in {"low", "medium", "high"}:
        errors.append(f"risk must be one of low/medium/high, got {parsed.get('risk')!r}")
    else:
        parsed["risk"] = risk

    approval = parsed.get("approval_required")
    if isinstance(approval, str):
        approval = approval.lower() in ("true", "yes", "1")
    elif not isinstance(approval, bool):
        approval = bool(approval)
    parsed["approval_required"] = approval
    if not approval:
        errors.append("approval_required must be true for every remediation proposal")

    # risk/approval defaults are acceptable fallbacks, but the above already forces them.
    parsed.setdefault("service", default_service)
    parsed.setdefault("evidence", "No evidence provided.")
    parsed.setdefault("risk", "medium")
    parsed.setdefault("approval_required", True)

    return len(errors) == 0, errors


def _parse_proposal(text: str, default_service: str) -> ActionProposal:
    """Parse and validate a model-generated ActionProposal.

    Returns an ActionProposal. If validation fails, the returned proposal has
    status="invalid" and error set; it never silently fabricates a remediation.
    """
    raw_text = text or ""
    parsed = _safe_parse_json(raw_text)
    valid, errors = _validate_proposal_fields(parsed, default_service)
    status = "pending" if valid else "invalid"
    error = "; ".join(errors) if errors else None

    if valid:
        return ActionProposal(
            action=parsed["action"],
            service=parsed["service"],
            evidence=parsed["evidence"],
            risk=parsed["risk"],
            approval_required=parsed["approval_required"],
            status=status,
            recalled_memory_ids=parsed.get("recalled_memory_ids", []),
            insufficient_evidence=parsed["insufficient_evidence"],
            error=error,
        )

    # Invalid: build a safe, explicit placeholder so callers can show the error and a retry button.
    action = str(parsed.get("action", "")).strip() or "none"
    service = str(parsed.get("service", "")).strip() or default_service
    evidence = str(parsed.get("evidence", "")).strip() or error or "Proposal validation failed."
    return ActionProposal(
        action=action,
        service=service,
        evidence=evidence,
        risk=str(parsed.get("risk", "")).strip().lower() if parsed.get("risk") else "medium",
        approval_required=True,
        status="invalid",
        recalled_memory_ids=parsed.get("recalled_memory_ids", []),
        insufficient_evidence=bool(parsed.get("insufficient_evidence", True)),
        error=error,
    )


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


async def _run_incident_unsafe(
    db: AsyncSession,
    alert: Alert,
    mode: Mode,
    event_queue: asyncio.Queue | None = None,
) -> dict[str, Any]:
    # Redact sensitive patterns from all textual alert fields before any model call or persistence.
    alert = alert.model_copy(
        update={
            "service": redact(alert.service),
            "symptom": redact(alert.symptom),
            "context": redact(alert.context),
        }
    )

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
            "If insufficient evidence, set action to 'none' and insufficient_evidence to true. "
            "Do not wrap the JSON in markdown fences."
        ),
    })

    proposal = await _resolve_final_proposal(messages, alert.service, recalled_ids, emit)

    result = {
        "id": run_id,
        "tenant": alert.tenant,
        "mode": mode,
        "alert": alert,
        "events": events,
        "proposal": proposal,
        "error": proposal.error if proposal and proposal.status == "invalid" else None,
    }
    emit("run.completed", {"proposal": proposal.model_dump() if proposal else None, "error": result["error"]})
    return result


async def run_incident(
    db: AsyncSession,
    alert: Alert,
    mode: Mode,
    event_queue: asyncio.Queue | None = None,
) -> dict[str, Any]:
    """Public wrapper that runs the agent and surfaces any failure as a structured error."""
    # Always redact all textual alert fields before any model call or persistence.
    alert = alert.model_copy(
        update={
            "service": redact(alert.service),
            "symptom": redact(alert.symptom),
            "context": redact(alert.context),
        }
    )

    try:
        return await _run_incident_unsafe(db, alert, mode, event_queue)
    except Exception as exc:
        run_id = str(uuid.uuid4())
        events: list[RunEvent] = []

        def emit(event_type: str, payload: dict[str, Any], **kwargs: Any) -> RunEvent:
            ev = _event(run_id, event_type, payload, **kwargs)
            events.append(ev)
            _maybe_enqueue(event_queue, ev)
            return ev

        emit("run.started", {"mode": mode, "alert": alert.model_dump()})
        emit("run.error", {"error": str(exc)})
        emit("run.completed", {"proposal": None, "error": str(exc)})
        return {
            "id": run_id,
            "tenant": alert.tenant,
            "mode": mode,
            "alert": alert,
            "events": events,
            "proposal": None,
            "error": str(exc),
        }


async def _resolve_final_proposal(
    messages: list[dict[str, Any]],
    service: str,
    recalled_ids: list[str],
    emit,
) -> ActionProposal:
    """Ask the model for a final ActionProposal, validate it, and retry once if invalid."""
    second = await qwen.chat(messages=messages, temperature=0.0, max_tokens=2048)
    emit(
        "model.proposal",
        {"content": second.get("content"), "validation": "pending"},
        model=second.get("model"),
        token_usage=second.get("token_usage"),
        latency_ms=second.get("latency_ms"),
    )

    proposal = _parse_proposal(second.get("content") or "", service)
    if proposal.status != "invalid":
        proposal.recalled_memory_ids = recalled_ids
        return proposal

    emit(
        "model.proposal.invalid",
        {"error": proposal.error, "content": second.get("content")},
    )

    correction = (
        "Your previous response failed validation with the following errors:\n"
        f"{proposal.error}\n\n"
        "Return a corrected JSON ActionProposal with exactly these fields and types: "
        "action (string), service (string), evidence (string), risk (one of low/medium/high), "
        "approval_required (boolean), insufficient_evidence (boolean). "
        "If evidence is insufficient, set action to 'none' and insufficient_evidence to true. "
        "Otherwise action must be a non-empty remediation. Do not wrap the JSON in markdown."
    )
    retry_messages = list(messages)
    retry_messages.append({"role": "assistant", "content": second.get("content") or ""})
    retry_messages.append({"role": "user", "content": correction})

    retry = await qwen.chat(messages=retry_messages, temperature=0.0, max_tokens=2048)
    emit(
        "model.proposal.retry",
        {
            "content": retry.get("content"),
            "previous_error": proposal.error,
        },
        model=retry.get("model"),
        token_usage=retry.get("token_usage"),
        latency_ms=retry.get("latency_ms"),
    )

    proposal = _parse_proposal(retry.get("content") or "", service)
    if proposal.status == "invalid":
        emit(
            "model.proposal.retry_failed",
            {"error": proposal.error, "content": retry.get("content")},
        )
    proposal.recalled_memory_ids = recalled_ids
    return proposal
