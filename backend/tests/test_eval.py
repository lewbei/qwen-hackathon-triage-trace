import json
from pathlib import Path

import pytest

from backend.app.eval import run_evaluation


@pytest.mark.asyncio
async def test_run_evaluation_13_scenario_benchmark(db_session):
    """End-to-end benchmark must complete and show memory mode beats stateless baseline."""
    scenarios_path = Path(__file__).parent.parent / "evaluations" / "scenarios.json"
    with scenarios_path.open() as f:
        scenarios = json.load(f)

    summary = await run_evaluation(db_session, scenarios, live=False)

    assert summary["evaluation_mode"] == "deterministic_mock"
    assert summary["scenarios"] == 13
    assert summary["memory_accuracy"] == pytest.approx(1.0)
    assert summary["memory_accuracy"] > summary["stateless_accuracy"]
    assert summary["memory_policy_compliance"] == pytest.approx(1.0)
    assert summary["poisoned_memory_recalled_count"] == 0
    assert summary["stale_memory_recalled_count"] == 0
    assert summary["irrelevant_memory_intrusion"] == 0
    assert all(
        r["policy_compliant"] for r in summary["results"] if r["mode"] == "memory"
    ), "no memory-mode proposal should violate policy"
