import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.demo import _action_matches, run_accumulation_demo, run_winning_scenario
from backend.app.memory import ACTIVE_STATUSES
from backend.app.schemas import ActionProposal


def _make_proposal(recalled_ids, action="Scale workers") -> ActionProposal:
    return ActionProposal(
        action=action,
        service="notification-service",
        evidence="test evidence",
        risk="low",
        approval_required=True,
        status="pending",
        recalled_memory_ids=recalled_ids,
        insufficient_evidence=False,
    )


async def _fake_run_incident(db, alert, mode):
    return {
        "id": "test-run-id",
        "tenant": alert.tenant,
        "mode": mode,
        "alert": alert,
        "events": [],
        "proposal": _make_proposal(recalled_ids=[] if mode.value == "stateless" else ["new-id"], action=f"{mode.value} action"),
    }


@pytest.mark.asyncio
async def test_winning_scenario_verdict(db_session):
    with patch("backend.app.demo.qwen.embed", new_callable=AsyncMock) as mock_embed, \
         patch("backend.app.demo.run_incident", new_callable=AsyncMock) as mock_run:
        # Deterministic 1536-dimensional embeddings so create_memory does not call Qwen.
        mock_embed.return_value = [[0.0] * 1536, [1.0] * 1536, [-1.0] * 1536]
        # The memory run will "recall" the new memory (its ID is not known yet, so we
        # use a side_effect that picks the new memory ID from the actual database rows).

        async def _run_incident_side_effect(db, alert, mode):
            from backend.app.schemas import Alert as AlertSchema
            from sqlalchemy import select
            from backend.app.models import MemoryRecord
            result = await db.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant == alert.tenant,
                    MemoryRecord.scope == "notification-service",
                )
            )
            rows = list(result.scalars().all())
            by_content = {m.content.split()[0]: m for m in rows}
            # "When" is the start of all three, but the next word differs. Distinguish by action verb.
            new_mem = next((m for m in rows if "requeue failed messages" in m.content.lower()), None)
            if mode.value == "stateless":
                recalled = []
            else:
                recalled = [str(new_mem.id)] if new_mem else []
            memory_action = (
                "Scale notification workers and requeue failed messages"
                if alert.service == "notification-service"
                else "Scale Redis and restart cart workers"
            )
            return {
                "id": f"test-run-{mode.value}",
                "tenant": alert.tenant,
                "mode": mode,
                "alert": alert,
                "events": [],
                "proposal": _make_proposal(recalled, action=memory_action if mode.value == "memory" else f"{mode.value} action"),
            }

        mock_run.side_effect = _run_incident_side_effect

        result = await run_winning_scenario(db_session, tenant="test-winning-demo")

    summary = result["summary"]
    memories = result["memories"]

    assert memories["old"]["status"] == "superseded"
    assert memories["new"]["status"] in ACTIVE_STATUSES
    assert memories["poison"]["status"] == "quarantined"
    assert summary["memory_firewall_passed"] is True
    assert summary["agent_behaviour_passed"] is True
    assert summary["demo_passed"] is True
    assert memories["new"]["id"] in summary["recalled_ids"]
    assert memories["old"]["id"] not in summary["recalled_ids"]
    assert memories["poison"]["id"] not in summary["recalled_ids"]


def test_action_matcher_requires_ordered_operation_target_pairs():
    winning_pairs = [
        {"operation": "scale", "targets": ["worker", "workers"]},
        {"operation": "requeue", "targets": ["message", "failed", "queue"]},
    ]
    assert _action_matches(
        "Scale notification workers and requeue failed messages",
        winning_pairs,
        ["restart", "delete", "refund", "drop"],
    ) is True
    # Reversed order must fail.
    assert _action_matches(
        "Requeue failed messages and scale notification workers",
        winning_pairs,
        ["restart", "delete", "refund", "drop"],
    ) is False
    # Each operation must be paired with an allowed target in its clause.
    assert _action_matches(
        "Scale Redis and restart cart workers",
        winning_pairs,
        ["restart", "delete", "refund", "drop"],
    ) is False


def test_action_matcher_allows_accumulation_required_pairs():
    accumulation_pairs = [
        {"operation": "scale", "targets": ["redis", "cache"]},
        {"operation": "restart", "targets": ["cart", "workers", "worker"]},
    ]
    assert _action_matches(
        "Scale Redis and restart cart workers",
        accumulation_pairs,
        ["database", "delete", "refund", "drop"],
    ) is True
    assert _action_matches(
        "Restart cart workers and scale Redis",
        accumulation_pairs,
        ["database", "delete", "refund", "drop"],
    ) is False
    assert _action_matches(
        "Scale the cart workers and restart Redis",
        accumulation_pairs,
        ["database", "delete", "refund", "drop"],
    ) is False


@pytest.mark.asyncio
async def test_accumulation_demo_verdict(db_session):
    with patch("backend.app.demo.qwen.embed", new_callable=AsyncMock) as mock_embed, \
         patch("backend.app.demo.run_incident", new_callable=AsyncMock) as mock_run:
        mock_embed.return_value = [[0.0] * 1536, [1.0] * 1536, [-1.0] * 1536]

        async def _run_incident_side_effect(db, alert, mode):
            from sqlalchemy import select
            from backend.app.models import MemoryRecord
            result = await db.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant == alert.tenant,
                    MemoryRecord.scope == "cart-service",
                )
            )
            rows = list(result.scalars().all())
            new_mem = next((m for m in rows if "restart the cart workers" in m.content.lower()), None)
            if mode.value == "stateless":
                recalled = []
            else:
                recalled = [str(new_mem.id)] if new_mem else []
            memory_action = (
                "Scale notification workers and requeue failed messages"
                if alert.service == "notification-service"
                else "Scale Redis and restart cart workers"
            )
            return {
                "id": f"test-run-{mode.value}",
                "tenant": alert.tenant,
                "mode": mode,
                "alert": alert,
                "events": [],
                "proposal": _make_proposal(recalled, action=memory_action if mode.value == "memory" else f"{mode.value} action"),
            }

        mock_run.side_effect = _run_incident_side_effect

        result = await run_accumulation_demo(db_session, tenant="test-accumulation-demo")

    summary = result["summary"]
    memories = result["memories"]

    assert memories["old"]["status"] == "superseded"
    assert memories["new"]["status"] in ACTIVE_STATUSES
    assert memories["poison"]["status"] == "quarantined"
    assert summary["memory_firewall_passed"] is True
    assert summary["agent_behaviour_passed"] is True
    assert summary["demo_passed"] is True
    assert memories["new"]["id"] in summary["recalled_ids"]
    assert memories["old"]["id"] not in summary["recalled_ids"]
    assert memories["poison"]["id"] not in summary["recalled_ids"]
