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
async def test_approved_run_creates_memory(db_session):
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
    assert decision.json()["status"] == "approved"
