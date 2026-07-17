from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.memory import ACTIVE_STATUSES, create_memory
from backend.app.models import MemoryRecord, RunRecord
from backend.app.schemas import ActionProposal, Alert
from backend.app.simulate import simulate_action


def _memory_disposition(memory: MemoryRecord) -> dict[str, Any]:
    """Return a stable, auditable summary of how the lifecycle treated the memory."""
    accepted = memory.status in ACTIVE_STATUSES
    return {
        "memory_accepted": accepted,
        "memory_status": memory.status,
        "memory_rejection_reason": (
            memory.meta.get("quarantine_reason") if not accepted else None
        ),
        "memory_id": str(memory.id),
    }


async def apply_operator_decision(
    session: AsyncSession,
    run_record: RunRecord,
    approved: bool,
    feedback: str,
    embedding: list[float] | None = None,
    subject: str | None = None,
    predicate: str = "remediation",
    source_timestamp: datetime | None = None,
    valid_from: datetime | None = None,
) -> dict[str, Any]:
    """Execute the post-approval simulation gate and persist the result.

    This is the internal approval workflow reused by the public decision endpoint
    and by controlled demos. It never trusts client-provided provenance.
    """
    proposal = ActionProposal(**run_record.proposal)
    alert = Alert(**run_record.alert)
    memory_subject = subject or alert.symptom

    # Try to use the metrics the agent actually observed during the run.
    observed_metrics: dict[str, Any] | None = None
    for ev in run_record.events or []:
        payload = ev.get("payload") or {}
        if ev.get("event_type") == "tools.called" and isinstance(payload, dict):
            for result in payload.get("results", []):
                if result.get("tool") == "inspect_metrics" and isinstance(result.get("result"), dict):
                    observed_metrics = result["result"]
                    break
            if observed_metrics:
                break

    outcome = simulate_action(proposal.service, proposal.action, observed_metrics)
    run_record.decision = {
        "approved": approved,
        "feedback": feedback,
        "outcome": outcome,
    }

    if approved and outcome["improved"]:
        # The simulator is only a predictive screen, not a real execution.
        memory = await create_memory(
            session,
            tenant=run_record.tenant,
            provenance="simulation",
            type="procedure",
            scope=proposal.service,
            subject=memory_subject,
            predicate="remediation",
            content=f"Simulated-safe procedure: {proposal.action}. Evidence: {proposal.evidence}. Simulated outcome: {outcome['reasoning']} (delta {outcome['delta']:+}). Operator feedback: {feedback}",
            source_authority=80,
            embedding=embedding,
            auto_embed=embedding is None,
            status="simulated_safe",
            source_timestamp=source_timestamp,
            valid_from=valid_from,
        )
        # create_memory owns the final status. If the lifecycle rejected the
        # memory (poison, duplicate, stale, lower authority, etc.), that status
        # must be reflected in the decision response and not overwritten.
        proposal.status = memory.status
        run_record.status = memory.status
        run_record.proposal = proposal.model_dump()
        run_record.decision = {**run_record.decision, "memory": _memory_disposition(memory)}
        await session.commit()
        return {
            "run_id": str(run_record.id),
            "approved": True,
            "simulated_safe": memory.status == "simulated_safe",
            "feedback": feedback,
            "status": memory.status,
            "memory_id": str(memory.id),
            "memory_accepted": memory.status in ACTIVE_STATUSES,
            "memory_status": memory.status,
            "memory_rejection_reason": _memory_disposition(memory)["memory_rejection_reason"],
            "outcome": outcome,
        }

    # Operator approved but the simulator predicts the action will worsen things.
    if approved:
        proposal.status = "rejected_by_simulation"
        run_record.status = "rejected_by_simulation"
        memory = await create_memory(
            session,
            tenant=run_record.tenant,
            provenance="failed_execution",
            type="preference",
            scope=proposal.service,
            subject=memory_subject,
            predicate="avoid",
            content=f"Avoid action: {proposal.action}. Simulated outcome: {outcome['reasoning']} (delta {outcome['delta']:+}). Operator feedback: {feedback}",
            source_authority=95,
            auto_embed=True,
        )
        run_record.proposal = proposal.model_dump()
        run_record.decision = {**run_record.decision, "memory": _memory_disposition(memory)}
        await session.commit()
        return {
            "run_id": str(run_record.id),
            "approved": False,
            "simulated_safe": False,
            "feedback": feedback,
            "status": "rejected_by_simulation",
            "memory_id": str(memory.id),
            "memory_accepted": memory.status in ACTIVE_STATUSES,
            "memory_status": memory.status,
            "memory_rejection_reason": None,
            "outcome": outcome,
        }

    # Operator explicitly rejected the proposal.
    proposal.status = "rejected"
    run_record.status = "rejected"
    memory = await create_memory(
        session,
        tenant=run_record.tenant,
        provenance="operator",
        type="preference",
        scope=proposal.service,
        subject=memory_subject,
        predicate="avoid",
        content=f"Rejected action: {proposal.action}. Operator feedback: {feedback}",
        source_authority=100,
        auto_embed=True,
    )
    run_record.proposal = proposal.model_dump()
    run_record.decision = {**run_record.decision, "memory": _memory_disposition(memory)}
    await session.commit()
    return {
        "run_id": str(run_record.id),
        "approved": False,
        "simulated_safe": False,
        "feedback": feedback,
        "status": "rejected",
        "memory_id": str(memory.id),
        "memory_accepted": memory.status in ACTIVE_STATUSES,
        "memory_status": memory.status,
        "memory_rejection_reason": None,
        "outcome": outcome,
    }
