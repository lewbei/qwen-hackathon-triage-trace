import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app


async def _fake_qwen_chat(*, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=1024, **kwargs):
    # First call: ask for evidence.
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
                {
                    "id": "call_2",
                    "function": {
                        "name": "read_current_runbook",
                        "arguments": json.dumps({"service": "cart-service"}),
                    },
                },
            ],
            "model": "qwen3.7-plus",
            "token_usage": {"prompt": 200, "completion": 80, "total": 280},
            "latency_ms": 1234.0,
        }
    # Second call: produce proposal.
    return {
        "content": json.dumps({
            "action": "Restart cart-service pods",
            "service": "cart-service",
            "evidence": "CPU is high and errors are elevated.",
            "risk": "medium",
            "approval_required": True,
            "insufficient_evidence": False,
        }),
        "model": "qwen3.7-plus",
        "token_usage": {"prompt": 400, "completion": 120, "total": 520},
        "latency_ms": 900.0,
    }


@pytest.mark.asyncio
async def test_stateless_run_creates_proposal():
    with patch("backend.app.agent.qwen") as mock_qwen:
        mock_qwen.chat = AsyncMock(side_effect=_fake_qwen_chat)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/agent/runs?mode=stateless",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate and slow checkout",
                    "severity": "critical",
                    "context": "Started after Redis latency spike",
                },
            )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "stateless"
    assert data["proposal"]["service"] == "cart-service"
    assert data["proposal"]["approval_required"] is True
    assert data["proposal"]["status"] == "pending"


@pytest.mark.asyncio
async def test_stateless_run_tolerates_missing_action():
    with patch("backend.app.agent.qwen") as mock_qwen:
        # First call requests tools; second call returns malformed proposal missing `action`.
        mock_qwen.chat = AsyncMock(side_effect=[
            {
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
                "token_usage": {"prompt": 200, "completion": 80, "total": 280},
                "latency_ms": 1234.0,
            },
            {
                "content": json.dumps({
                    "service": "cart-service",
                    "evidence": "CPU is high and errors are elevated.",
                    "risk": "medium",
                    "approval_required": True,
                    "insufficient_evidence": False,
                }),
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 400, "completion": 120, "total": 520},
                "latency_ms": 900.0,
            },
        ])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/agent/runs?mode=stateless",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate and slow checkout",
                    "severity": "critical",
                    "context": "Started after Redis latency spike",
                },
            )
    assert response.status_code == 200
    data = response.json()
    assert data["proposal"]["action"] == "none"
    assert data["proposal"]["status"] == "pending"


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
