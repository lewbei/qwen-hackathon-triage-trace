from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.memory import create_memory
from backend.app.models import RunRecord
from backend.app.schemas import ActionProposal, Alert
from backend.app.simulate import simulate_action


async def apply_operator_decision(
    session: AsyncSession,
    run_record: RunRecord,
    approved: bool,
    feedback: str,
    embedding: list[float] | None = None,
    subject: str | None = None,
    predicate: str = "remediation",
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
        proposal.status = "simulated_safe"
        run_record.status = "simulated_safe"
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
        )
        memory.status = "simulated_safe"
        run_record.proposal = proposal.model_dump()
        await session.commit()
        return {
            "run_id": str(run_record.id),
            "approved": True,
            "validated": False,
            "simulated_safe": True,
            "feedback": feedback,
            "status": "simulated_safe",
            "memory_id": str(memory.id),
            "outcome": outcome,
        }

    # Operator approved but the simulator predicts the action will worsen things.
    if approved:
        proposal.status = "rejected_by_simulation"
        run_record.status = "rejected_by_simulation"
        await create_memory(
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
        await session.commit()
        return {
            "run_id": str(run_record.id),
            "approved": False,
            "validated": False,
            "simulated_safe": False,
            "feedback": feedback,
            "status": "rejected_by_simulation",
            "outcome": outcome,
        }

    # Operator explicitly rejected the proposal.
    proposal.status = "rejected"
    run_record.status = "rejected"
    await create_memory(
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
    await session.commit()
    return {
        "run_id": str(run_record.id),
        "approved": False,
        "validated": False,
        "simulated_safe": False,
        "feedback": feedback,
        "status": "rejected",
        "outcome": outcome,
    }
