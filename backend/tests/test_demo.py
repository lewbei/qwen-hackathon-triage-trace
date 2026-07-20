from unittest.mock import AsyncMock, patch

import pytest

from backend.app import action_rules
from backend.app.demo import run_accumulation_demo, run_winning_scenario
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


def test_action_rules_pass_on_expected_cart_action():
    result = action_rules.evaluate_action(
        "Scale the Redis cache and restart the cart workers", "cart_redis_recovery"
    )
    assert result["passed"] is True
    assert result["forbidden_matches"] == []


def test_action_rules_fail_on_reversed_operations():
    result = action_rules.evaluate_action(
        "Restart cart workers and scale Redis", "cart_redis_recovery"
    )
    assert result["passed"] is False
    assert any("restart cart workers" in rc for rc in result["reason_codes"])


def test_action_rules_fail_on_forbidden_cart_action():
    result = action_rules.evaluate_action(
        "Restart the database and delete all pending user carts", "cart_redis_recovery"
    )
    assert result["passed"] is False
    assert result["forbidden_matches"]


def test_action_rules_pass_on_notification_action():
    result = action_rules.evaluate_action(
        "Scale the notification workers and requeue failed messages", "notification_backlog_recovery"
    )
    assert result["passed"] is True


def test_action_rules_fail_on_delete_queue():
    result = action_rules.evaluate_action(
        "Delete the entire message queue and refund all users", "notification_backlog_recovery"
    )
    assert result["passed"] is False
    assert result["forbidden_matches"]


def test_action_rules_pass_on_payment_action():
    result = action_rules.evaluate_action(
        "Verify PSP connectivity and fail over to backup PSP", "payment_psp_failover"
    )
    assert result["passed"] is True


def test_action_rules_fail_on_refund_and_bypass():
    result = action_rules.evaluate_action(
        "Refund everything and bypass all payment checks", "payment_psp_failover"
    )
    assert result["passed"] is False
    assert result["forbidden_matches"]


def test_action_rules_clause_aware_negation():
    result = action_rules.evaluate_action(
        "Do not restart the database, then scale Redis and restart the cart workers", "cart_redis_recovery"
    )
    assert result["passed"] is True
    assert result["forbidden_matches"] == []


def test_action_rules_compound_harmful_overrides_safe():
    result = action_rules.evaluate_action(
        "Scale the Redis cache and restart the cart workers, then delete all pending user carts", "cart_redis_recovery"
    )
    assert result["passed"] is False
    assert "delete carts" in " ".join(result["forbidden_matches"])


def test_action_rules_partial_payment_action_fails():
    result = action_rules.evaluate_action(
        "Verify PSP connectivity", "payment_psp_failover"
    )
    assert result["passed"] is False
    assert any("fail over to backup psp" in rc for rc in result["reason_codes"])


def test_action_rules_contraction_negation_dont():
    result = action_rules.evaluate_action(
        "Don't delete carts; scale Redis and restart cart workers", "cart_redis_recovery"
    )
    assert result["passed"] is True
    assert result["forbidden_matches"] == []


def test_action_rules_contraction_negation_doesnt():
    result = action_rules.evaluate_action(
        "It doesn't delete the queue; scale notification workers and requeue failed messages",
        "notification_backlog_recovery",
    )
    assert result["passed"] is True
    assert result["forbidden_matches"] == []


def test_action_rules_contraction_negation_cant_cannot():
    assert action_rules.evaluate_action(
        "Can't refund everything; verify PSP connectivity and fail over to backup PSP",
        "payment_psp_failover",
    )["passed"] is True
    assert action_rules.evaluate_action(
        "Cannot bypass payment checks; verify PSP connectivity and fail over to backup PSP",
        "payment_psp_failover",
    )["passed"] is True


@pytest.mark.asyncio
async def test_winning_scenario_verdict(db_session):
    with patch("backend.app.demo.qwen.embed", new_callable=AsyncMock) as mock_embed, \
         patch("backend.app.demo.run_incident", new_callable=AsyncMock) as mock_run:
        mock_embed.return_value = [[0.0] * 1536, [1.0] * 1536, [-1.0] * 1536]

        async def _run_incident_side_effect(db, alert, mode):
            from sqlalchemy import select
            from backend.app.models import MemoryRecord
            result = await db.execute(
                select(MemoryRecord).where(
                    MemoryRecord.tenant == alert.tenant,
                    MemoryRecord.scope == "notification-service",
                )
            )
            rows = list(result.scalars().all())
            new_mem = next((m for m in rows if "scale the notification workers and requeue failed messages" in m.content.lower()), None)
            if mode.value == "stateless":
                recalled = []
            else:
                recalled = [str(new_mem.id)] if new_mem else []
            memory_action = (
                "Scale the notification workers and requeue failed messages"
                if alert.service == "notification-service"
                else "Scale the Redis cache and restart the cart workers"
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
            new_mem = next((m for m in rows if "scale the redis cache and restart the cart workers" in m.content.lower()), None)
            if mode.value == "stateless":
                recalled = []
            else:
                recalled = [str(new_mem.id)] if new_mem else []
            memory_action = (
                "Scale the notification workers and requeue failed messages"
                if alert.service == "notification-service"
                else "Scale the Redis cache and restart the cart workers"
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
