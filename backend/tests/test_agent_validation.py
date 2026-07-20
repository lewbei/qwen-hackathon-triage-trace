import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import agent as agent_module
from backend.app.agent import _parse_proposal, _validate_proposal_fields
from backend.app.config import settings
from backend.app.main import app
from backend.app.memory import create_memory
from backend.app.qwen import qwen
from backend.app.schemas import Mode
from backend.tests.conftest import TEST_DEMO_SECRET


def test_parse_proposal_strips_markdown_fences():
    text = (
        "```json\n"
        + json.dumps({
            "action": "Restart cart-service pods",
            "service": "cart-service",
            "evidence": "CPU high.",
            "risk": "medium",
            "approval_required": True,
            "insufficient_evidence": False,
        })
        + "\n```"
    )
    proposal = _parse_proposal(text, "cart-service")
    assert proposal.status == "pending"
    assert proposal.action == "Restart cart-service pods"


def test_parse_proposal_rejects_missing_action():
    proposal = _parse_proposal(
        json.dumps({
            "service": "cart-service",
            "evidence": "CPU high.",
            "risk": "medium",
            "approval_required": True,
            "insufficient_evidence": False,
        }),
        "cart-service",
    )
    assert proposal.status == "invalid"
    assert "action" in (proposal.error or "").lower()


def test_parse_proposal_rejects_empty_action():
    proposal = _parse_proposal(
        json.dumps({
            "action": "",
            "service": "cart-service",
            "evidence": "CPU high.",
            "risk": "medium",
            "approval_required": True,
            "insufficient_evidence": False,
        }),
        "cart-service",
    )
    assert proposal.status == "invalid"
    assert "action" in (proposal.error or "").lower()


def test_parse_proposal_rejects_none_without_insufficient_evidence():
    proposal = _parse_proposal(
        json.dumps({
            "action": "none",
            "service": "cart-service",
            "evidence": "No data.",
            "risk": "low",
            "approval_required": True,
            "insufficient_evidence": False,
        }),
        "cart-service",
    )
    assert proposal.status == "invalid"
    assert "insufficient_evidence" in (proposal.error or "").lower()


def test_validate_proposal_allows_none_with_insufficient_evidence():
    valid, errors = _validate_proposal_fields({
        "action": "none",
        "service": "cart-service",
        "evidence": "No data available.",
        "risk": "low",
        "approval_required": True,
        "insufficient_evidence": True,
    }, "cart-service")
    assert valid is True
    assert not errors


def test_validate_proposal_rejects_false_approval_required():
    valid, errors = _validate_proposal_fields({
        "action": "Restart cart-service pods",
        "service": "cart-service",
        "evidence": "CPU high.",
        "risk": "medium",
        "approval_required": False,
        "insufficient_evidence": False,
    }, "cart-service")
    assert valid is False
    assert any("approval" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_run_surfaces_model_timeout_as_error(db_session):
    with patch("backend.app.agent.qwen") as mock_qwen:
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
            TimeoutError("model call timed out"),
        ])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/agent/runs?mode=stateless",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
                headers={"x-demo-secret": TEST_DEMO_SECRET},
            )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "timed out" in (data.get("error") or "").lower()


@pytest.mark.asyncio
async def test_run_tolerates_embedding_failure_and_returns_proposal(db_session, monkeypatch):
    async def _failing_embed(*args, **kwargs):
        raise RuntimeError("embedding endpoint unavailable")

    monkeypatch.setattr(qwen, "embed", _failing_embed)

    with patch("backend.app.agent.qwen.chat") as mock_chat:
        mock_chat.return_value = {
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
        }
        # Second call (final proposal) is via the same patched qwen.chat, so set side_effect.
        mock_chat.side_effect = [
            mock_chat.return_value,
            {
                "content": json.dumps({
                    "action": "Restart cart-service pods",
                    "service": "cart-service",
                    "evidence": "CPU high.",
                    "risk": "medium",
                    "approval_required": True,
                    "insufficient_evidence": False,
                }),
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 400, "completion": 120, "total": 520},
                "latency_ms": 900.0,
            },
        ]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/agent/runs?mode=memory",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
                headers={"x-demo-secret": TEST_DEMO_SECRET},
            )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert any(e["event_type"] == "memory.embed_failed" for e in data["events"])
    assert data["proposal"]["action"] == "Restart cart-service pods"


@pytest.mark.asyncio
async def test_run_records_unknown_tool_calls(db_session):
    with patch("backend.app.agent.qwen") as mock_qwen:
        mock_qwen.chat = AsyncMock(side_effect=[
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "unknown_tool",
                            "arguments": json.dumps({"service": "cart-service"}),
                        },
                    },
                ],
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 200, "completion": 80, "total": 280},
                "latency_ms": 1234.0,
            },
            {
                "content": json.dumps({
                    "action": "Restart cart-service pods",
                    "service": "cart-service",
                    "evidence": "CPU high despite unknown tool.",
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
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
                headers={"x-demo-secret": TEST_DEMO_SECRET},
            )
    assert response.status_code == 200
    data = response.json()
    assert data["proposal"]["status"] == "pending"
    tool_event = next(e for e in data["events"] if e["event_type"] == "tools.called")
    results = tool_event["payload"]["results"]
    assert any(r["tool"] == "unknown_tool" and "error" in r["result"] for r in results)


@pytest.mark.asyncio
async def test_memory_run_marks_invalid_proposal_and_preserves_recall(db_session, monkeypatch):
    """A safe memory is recalled, but the model returns an invalid proposal.

    The response must expose the invalid proposal, record the recall, and not
    silently fabricate a remediation.
    """
    async def _distinct_embed(texts: list[str], dimensions: int = 1536) -> list[list[float]]:
        return [[1.0] * dimensions for _ in texts]

    monkeypatch.setattr(qwen, "embed", _distinct_embed)

    # Seed a safe current memory so retrieval can recall it.
    safe = await create_memory(
        db_session,
        tenant="default",
        provenance="simulation",
        type="procedure",
        scope="cart-service",
        subject="checkout_failures",
        predicate="remediation",
        content="Scale the Redis cache and restart the cart workers",
        source_authority=80,
        source_timestamp=agent_module._now(),
        embedding=[1.0] * 1536,
        auto_embed=False,
    )
    assert safe.status == "active"

    with patch("backend.app.agent.qwen") as mock_qwen, \
         patch("backend.app.memory.qwen.rerank", new_callable=AsyncMock) as mock_rerank:
        mock_rerank.return_value = {}  # forces cosine fallback
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
            # First final proposal: invalid (missing action).
            {
                "content": json.dumps({
                    "service": "cart-service",
                    "evidence": "CPU high.",
                    "risk": "medium",
                    "approval_required": True,
                    "insufficient_evidence": False,
                }),
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 400, "completion": 120, "total": 520},
                "latency_ms": 900.0,
            },
            # Retry still invalid (empty action).
            {
                "content": json.dumps({
                    "action": "",
                    "service": "cart-service",
                    "evidence": "Still broken.",
                    "risk": "medium",
                    "approval_required": True,
                    "insufficient_evidence": False,
                }),
                "model": "qwen3.7-plus",
                "token_usage": {"prompt": 400, "completion": 120, "total": 520},
                "latency_ms": 910.0,
            },
        ])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/agent/runs?mode=memory",
                json={
                    "service": "cart-service",
                    "symptom": "High checkout failure rate",
                    "context": "Redis spike",
                },
                headers={"x-demo-secret": TEST_DEMO_SECRET},
            )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "invalid"
    assert data["proposal"]["status"] == "invalid"
    assert data["proposal"]["error"]
    assert str(safe.id) in data["proposal"]["recalled_memory_ids"]


@pytest.mark.asyncio
async def test_approved_harmful_compound_action_rejected_by_simulation(db_session):
    """A compound action containing a harmful keyword must be predicted to worsen."""
    async def _compound_chat(*, messages, tools=None, tool_choice=None, **kwargs):
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
                "action": "Scale workers and delete the database",
                "service": "cart-service",
                "evidence": "CPU high.",
                "risk": "high",
                "approval_required": True,
                "insufficient_evidence": False,
            }),
            "model": "qwen3.7-plus",
            "token_usage": {"prompt": 100, "completion": 40, "total": 140},
            "latency_ms": 500.0,
        }

    with patch("backend.app.agent.qwen") as mock_qwen:
        mock_qwen.chat = AsyncMock(side_effect=_compound_chat)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            run_res = await client.post(
                "/api/agent/runs?mode=memory",
                json={
                    "service": "cart-service",
                    "symptom": "High error rate",
                    "context": "Redis spike",
                },
                headers={"x-demo-secret": TEST_DEMO_SECRET},
            )
    assert run_res.status_code == 200
    run_id = run_res.json()["id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        decision = await client.post(
            f"/api/proposals/{run_id}/decision",
            json={"approved": True, "feedback": "try it"},
            headers={"x-demo-secret": TEST_DEMO_SECRET},
        )
    assert decision.status_code == 200
    data = decision.json()
    assert data["status"] == "rejected_by_simulation"
    assert data["simulated_safe"] is False
    assert data["outcome"]["improved"] is False


@pytest.mark.asyncio
async def test_alert_service_and_symptom_are_redacted(db_session, monkeypatch):
    """Sensitive credential patterns in service/symptom/context must be redacted before prompts."""
    from backend.app.schemas import Alert

    captured_messages: list[list[dict[str, str]]] = []

    async def _capture_chat(*, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=1024, **kwargs):
        captured_messages.append(messages)
        return {
            "content": json.dumps({
                "action": "none",
                "service": "cart-service",
                "evidence": "Insufficient evidence",
                "risk": "low",
                "approval_required": False,
                "insufficient_evidence": True,
            }),
            "model": "qwen3.7-plus",
            "token_usage": {"prompt": 100, "completion": 50, "total": 150},
            "latency_ms": 500.0,
        }

    monkeypatch.setattr(qwen, "chat", _capture_chat)
    monkeypatch.setattr(qwen, "embed", AsyncMock(return_value=[[0.0] * 1536]))

    alert = Alert(
        tenant="default",
        service="cart-service api-key=super-secret",
        symptom="password=leaked redis timeout",
        context="Redis latency spiked; token=abc123",
        severity="critical",
    )
    await agent_module.run_incident(db_session, alert, Mode.stateless)

    assert captured_messages
    prompt_text = json.dumps(captured_messages[-1])
    assert "super-secret" not in prompt_text
    assert "leaked" not in prompt_text
    assert "abc123" not in prompt_text


@pytest.mark.asyncio
async def test_embed_falls_back_to_v3_when_v4_unavailable():
    """If the configured embedding model is unavailable, QwenGateway.embed retries with the fallback model."""
    from openai import APIError

    from backend.app.config import settings
    from backend.app.qwen import QwenGateway

    calls: list[dict[str, Any]] = []

    async def _fake_create(self, *, input, model, dimensions, **kwargs):
        calls.append({"model": model, "input": input, "dimensions": dimensions})
        if model == settings.qwen_embedding_model:
            err = APIError("The model text-embedding-v4 is not found.", request=None)
            err.code = "model_not_found"
            raise err
        return type("Resp", (), {"data": [type("Item", (), {"embedding": [0.1] * dimensions})()]})()

    gateway = QwenGateway(api_key="test-key")
    gateway.embedding_client = type(
        "FakeClient",
        (),
        {"embeddings": type("FakeEmbeddings", (), {"create": _fake_create})()},
    )()

    result = await gateway.embed(["test"], dimensions=1536)

    assert len(calls) == 2
    assert calls[0]["model"] == settings.qwen_embedding_model
    assert calls[1]["model"] == settings.qwen_embedding_fallback_model
    assert result == [[0.1] * 1536]
