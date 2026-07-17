import json
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
async def test_approved_validated_run_creates_memory(db_session):
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
    assert data["status"] == "validated"
    assert data["validated"] is True
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
    assert data["validated"] is False
    assert data["outcome"]["improved"] is False
