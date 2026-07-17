import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app


async def _fake_qwen_chat(*, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=1024, **kwargs):
    if tools and tool_choice:
        return {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "inspect_metrics",
                        "arguments": json.dumps({"service": "cart-service", "time_window": "1h"}),
                    },
                },
            ],
            "model": "qwen3.7-plus",
            "token_usage": {"prompt": 100, "completion": 40, "total": 140},
            "latency_ms": 500.0,
        }
    return {
        "content": json.dumps({
            "action": "Restart cart-service pods",
            "service": "cart-service",
            "evidence": "CPU high.",
            "risk": "medium",
            "approval_required": True,
            "insufficient_evidence": False,
        }),
        "model": "qwen3.7-plus",
        "token_usage": {"prompt": 100, "completion": 40, "total": 140},
        "latency_ms": 500.0,
    }


@pytest.mark.asyncio
async def test_approved_simulated_safe_run_creates_memory(db_session):
    with patch("backend.app.agent.qwen") as mock_qwen:
        mock_qwen.chat = AsyncMock(side_effect=_fake_qwen_chat)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            run_res = await client.post(
                "/api/agent/runs?mode=memory",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
            )
    assert run_res.status_code == 200
    run_id = run_res.json()["id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decision = await client.post(f"/api/proposals/{run_id}/decision", json={"approved": True, "feedback": "Worked"})
    assert decision.status_code == 200
    data = decision.json()
    assert data["status"] == "simulated_safe"
    assert data["simulated_safe"] is True
    assert data["outcome"]["improved"] is True
    assert "memory_id" in data


@pytest.mark.asyncio
async def test_approved_run_rejected_by_bad_simulation(db_session):
    """A restart-database proposal that is approved by the operator should still be
    rejected because the simulator predicts it will make checkout errors worse.
    """

    async def _bad_action_chat(*, messages, tools=None, tool_choice=None, **kwargs):
        if tools and tool_choice:
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "inspect_metrics",
                            "arguments": json.dumps({"service": "cart-service", "time_window": "1h"}),
                        },
                    },
                ],
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 100, "completion": 40, "total": 140},
                "latency_ms": 500.0,
            }
        return {
            "content": json.dumps({
                "action": "Restart the database",
                "service": "cart-service",
                "evidence": "CPU high.",
                "risk": "medium",
                "approval_required": True,
                "insufficient_evidence": False,
            }),
            "model": "qwen3.7-plus",
            "token_usage": {"prompt": 100, "completion": 40, "total": 140},
            "latency_ms": 500.0,
        }

    with patch("backend.app.agent.qwen") as mock_qwen:
        mock_qwen.chat = AsyncMock(side_effect=_bad_action_chat)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            run_res = await client.post(
                "/api/agent/runs?mode=memory",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
            )
    assert run_res.status_code == 200
    run_id = run_res.json()["id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decision = await client.post(f"/api/proposals/{run_id}/decision", json={"approved": True, "feedback": "try it"})
    assert decision.status_code == 200
    data = decision.json()
    assert data["status"] == "rejected_by_simulation"
    assert data["simulated_safe"] is False
    assert data["outcome"]["improved"] is False


@pytest.mark.asyncio
async def test_lifecycle_rejection_overrides_decision_status(db_session):
    """If create_memory rejects a duplicate, apply_operator_decision must not
    resurrect the status to simulated_safe."""
    from backend.app.decisions import apply_operator_decision
    from backend.app.models import MemoryRecord, RunRecord
    from backend.app.schemas import ActionProposal, Alert

    tenant = "test-rejection-final"
    alert = Alert(
        tenant=tenant,
        service="notification-service",
        symptom="queue backlog",
        context="workers cannot keep up",
        severity="warning",
    )
    proposal = ActionProposal(
        action="Scale workers and requeue messages",
        service="notification-service",
        evidence="drains backlog without dropping messages",
        risk="low",
        approval_required=True,
        status="pending",
        recalled_memory_ids=[],
        insufficient_evidence=False,
    )
    run = RunRecord(
        tenant=tenant,
        mode="memory",
        alert=alert.model_dump(),
        proposal=proposal.model_dump(),
        events=[],
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    with patch("backend.app.memory.qwen.embed", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.0] * 1536]
        first = await apply_operator_decision(db_session, run, approved=True, feedback="ok")
    assert first["status"] == "simulated_safe"

    run2 = RunRecord(
        tenant=tenant,
        mode="memory",
        alert=alert.model_dump(),
        proposal=proposal.model_dump(),
        events=[],
        status="running",
    )
    db_session.add(run2)
    await db_session.commit()

    with patch("backend.app.memory.qwen.embed", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.0] * 1536]
        second = await apply_operator_decision(db_session, run2, approved=True, feedback="ok")

    assert second["status"] == "quarantined"
    assert second["simulated_safe"] is False
    second_mem = await db_session.get(MemoryRecord, uuid.UUID(second["memory_id"]))
    assert second_mem is not None
    assert second_mem.status == "quarantined"
    await db_session.refresh(run2)
    assert run2.status == "quarantined"


@pytest.mark.asyncio
async def test_historical_timestamps_propagate_to_memory(db_session):
    """Historical source_timestamp and valid_from supplied to the approval gate
    must be stored and used to compute expires_at, not ignored."""
    from datetime import datetime, timedelta, timezone
    from backend.app.decisions import apply_operator_decision
    from backend.app.models import MemoryRecord, RunRecord
    from backend.app.schemas import ActionProposal, Alert

    tenant = "test-historical-ttl"
    alert = Alert(
        tenant=tenant,
        service="notification-service",
        symptom="queue backlog",
        context="workers cannot keep up",
        severity="warning",
    )
    proposal = ActionProposal(
        action="Scale workers and requeue messages",
        service="notification-service",
        evidence="drains backlog without dropping messages",
        risk="low",
        approval_required=True,
        status="pending",
        recalled_memory_ids=[],
        insufficient_evidence=False,
    )
    run = RunRecord(
        tenant=tenant,
        mode="memory",
        alert=alert.model_dump(),
        proposal=proposal.model_dump(),
        events=[],
        status="running",
    )
    db_session.add(run)
    await db_session.commit()

    past = datetime.now(timezone.utc) - timedelta(days=5)
    with patch("backend.app.memory.qwen.embed", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.0] * 1536]
        result = await apply_operator_decision(
            db_session,
            run,
            approved=True,
            feedback="ok",
            source_timestamp=past,
            valid_from=past,
        )

    assert result["status"] == "simulated_safe"
    mem = await db_session.get(MemoryRecord, uuid.UUID(result["memory_id"]))
    assert mem.source_timestamp == past
    assert mem.valid_from == past
    assert mem.expires_at is not None
    assert (mem.expires_at - mem.valid_from).days == 14
