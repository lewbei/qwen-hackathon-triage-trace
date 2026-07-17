from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent import run_incident
from backend.app.memory import create_memory
from backend.app.models import MemoryRecord
from backend.app.qwen import qwen as default_qwen
from backend.app.schemas import Alert


class MockQwen:
    """Deterministic Qwen stand-in for evaluation. Returns unsafe baseline in stateless
    mode and correct action when validated memory context is present."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.calls: list[dict] = []
        self.total_tokens = {"prompt": 0, "completion": 0, "total": 0}

    async def chat(self, *, messages, tools=None, tool_choice=None, temperature=0.2, max_tokens=1024, **kwargs):
        if tools and tool_choice:
            # First call: request evidence tools.
            return {
                "content": None,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "function": {
                            "name": "inspect_metrics",
                            "arguments": json.dumps({"service": self.scenario["alert"]["service"], "time_window": "1h"}),
                        },
                    },
                    {
                        "id": "tc2",
                        "function": {
                            "name": "read_current_runbook",
                            "arguments": json.dumps({"service": self.scenario["alert"]["service"]}),
                        },
                    },
                ],
                "model": "qwen3.7-plus-mock",
                "token_usage": {"prompt": 200, "completion": 120, "total": 320},
                "latency_ms": 100.0,
            }

        # Second call: produce proposal.
        memory_present = any("Relevant validated experience" in (m.get("content") or "") for m in messages)
        if memory_present:
            action = self.scenario["expected_action"]
            risk = "low"
            insufficient = self.scenario.get("insufficient_evidence", False)
        else:
            action = self.scenario.get("unsafe_action", "generic restart")
            risk = "high" if self.scenario.get("policy_violation") else "medium"
            insufficient = self.scenario.get("insufficient_evidence", False)

        return {
            "content": json.dumps({
                "action": action,
                "service": self.scenario["alert"]["service"],
                "evidence": "Mock evidence for evaluation.",
                "risk": risk,
                "approval_required": True,
                "insufficient_evidence": insufficient,
            }),
            "model": "qwen3.7-plus-mock",
            "token_usage": {"prompt": 300, "completion": 80, "total": 380},
            "latency_ms": 150.0,
        }

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Deterministic 1536-dim embedding (all zeros; real evaluation should use text-embedding-v4).
        return [[0.0] * 1536 for _ in texts]


async def _clear_eval_memories(session: AsyncSession, tenant: str) -> None:
    await session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
    await session.commit()


async def seed_scenario(session: AsyncSession, scenario: dict[str, Any]) -> list[str]:
    seeds = scenario.get("seed_memory")
    if not seeds:
        return []
    if isinstance(seeds, dict):
        seeds = [seeds]
    ids = []
    for seed in seeds:
        record = await create_memory(
            session,
            tenant=scenario["alert"]["tenant"],
            provenance=seed.get("provenance", "runbook"),
            type=seed["type"],
            scope=seed["scope"],
            subject=seed["subject"],
            predicate=seed["predicate"],
            content=seed["content"],
            source_authority=100 if seed.get("provenance") == "operator" else 80,
        )
        ids.append(str(record.id))
    return ids


async def evaluate_scenario(session: AsyncSession, scenario: dict[str, Any], mode: str, qwen_mock: MockQwen | None = None) -> dict[str, Any]:
    from backend.app import agent as agent_module
    from backend.app import memory as memory_module
    from backend.app import qwen as qwen_module

    original_qwen = agent_module.qwen
    if qwen_mock:
        agent_module.qwen = qwen_mock
        memory_module.qwen = qwen_mock
        qwen_module.qwen = qwen_mock

    try:
        start = time.perf_counter()
        alert = Alert(**scenario["alert"])
        run = await run_incident(session, alert, mode)
        latency_ms = (time.perf_counter() - start) * 1000
        proposal = run["proposal"]
        expected = scenario.get("expected_action", "")
        action = proposal.action.lower()
        correct = expected and expected in action
        policy_ok = not (scenario.get("policy_violation") and scenario.get("unsafe_action") and scenario["unsafe_action"] in action)
        return {
            "scenario_id": scenario["id"],
            "category": scenario["category"],
            "mode": mode,
            "action": proposal.action,
            "expected": expected,
            "correct": correct,
            "policy_compliant": policy_ok,
            "status": proposal.status,
            "risk": proposal.risk,
            "recalled_memory_ids": proposal.recalled_memory_ids,
            "latency_ms": latency_ms,
            "total_tokens": 700,  # mock
            "injected_tokens": 0 if mode == "stateless" else sum(len(mid) for mid in proposal.recalled_memory_ids) * 10,
        }
    finally:
        agent_module.qwen = original_qwen
        memory_module.qwen = original_qwen
        qwen_module.qwen = original_qwen


async def run_evaluation(session: AsyncSession, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    tenant = scenarios[0]["alert"]["tenant"] if scenarios else "eval"
    await _clear_eval_memories(session, tenant)
    for scenario in scenarios:
        scenario["alert"]["tenant"] = tenant
        # Stateless baseline
        mock = MockQwen(scenario)
        result_stateless = await evaluate_scenario(session, scenario, "stateless", mock)
        results.append(result_stateless)

        # Memory treatment
        await _clear_eval_memories(session, tenant)
        memory_ids = await seed_scenario(session, scenario)
        mock2 = MockQwen(scenario)
        result_memory = await evaluate_scenario(session, scenario, "memory", mock2)
        results.append(result_memory)
        await _clear_eval_memories(session, tenant)

    stateless_correct = sum(1 for r in results if r["mode"] == "stateless" and r["correct"])
    memory_correct = sum(1 for r in results if r["mode"] == "memory" and r["correct"])
    stateless_total = sum(1 for r in results if r["mode"] == "stateless")
    memory_total = sum(1 for r in results if r["mode"] == "memory")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scenarios": len(scenarios),
        "stateless_accuracy": stateless_correct / stateless_total if stateless_total else 0,
        "memory_accuracy": memory_correct / memory_total if memory_total else 0,
        "delta_accuracy": (memory_correct - stateless_correct) / stateless_total if stateless_total else 0,
        "stateless_policy_compliance": sum(1 for r in results if r["mode"] == "stateless" and r["policy_compliant"]) / stateless_total if stateless_total else 0,
        "memory_policy_compliance": sum(1 for r in results if r["mode"] == "memory" and r["policy_compliant"]) / memory_total if memory_total else 0,
        "results": results,
    }
    return summary
