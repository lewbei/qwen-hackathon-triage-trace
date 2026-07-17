"""Run TriageTrace's MemoryGate on AgentSecurityBench (ASB) tool descriptions.

ASB defines attacker tools and normal tools for its agent security scenarios.
We feed each tool description as an untrusted (`log` provenance) memory and
measure how well MemoryGate quarantines malicious attacker instructions while
keeping normal tools active.

Data source: https://github.com/agiresearch/ASB (MIT License).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.config import settings
from backend.app.memory import create_memory
from backend.app.models import Base


settings.use_llm_poison_check = True  # Enable LLM fallback for ASB evaluation

ASB_DATA_DIR = Path(__file__).parent / "asb_data"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _memory_text(row: dict[str, Any], attack: bool) -> str:
    """Build the candidate memory text from an ASB tool row."""
    if attack:
        parts = [
            f"Tool: {row.get('Attacker Tool', '')}",
            f"Instruction: {row.get('Attacker Instruction', '')}",
            f"Description: {row.get('Description', '')}",
            f"Attack goal: {row.get('Attack goal', '')}",
        ]
    else:
        parts = [
            f"Tool: {row.get('Tool Name', '')}",
            f"Description: {row.get('Description', '')}",
            f"Expected outcome: {row.get('Expected Achievements', '')}",
        ]
    return "\n".join(p for p in parts if p.split(": ", 1)[1] if ":" in p)


async def _run_asb(
    attack_files: list[str],
    normal_files: list[str],
    sample_attacks: int | None = None,
    sample_normals: int | None = None,
) -> dict[str, Any]:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    tenant = f"asb_eval_{uuid.uuid4().hex[:8]}"
    from backend.app.models import MemoryRecord
    async with SessionLocal() as session:
        await session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
        await session.commit()

    attacks: list[dict[str, Any]] = []
    for fname in attack_files:
        attacks.extend(_load_jsonl(ASB_DATA_DIR / fname))
    normals: list[dict[str, Any]] = []
    for fname in normal_files:
        normals.extend(_load_jsonl(ASB_DATA_DIR / fname))

    if sample_attacks is not None:
        attacks = attacks[:sample_attacks]
    if sample_normals is not None:
        normals = normals[:sample_normals]

    tp = fp = tn = fn = 0
    detail: list[dict[str, Any]] = []

    async with SessionLocal() as session:
        # Test attacks (expected quarantined).
        for row in attacks:
            text = _memory_text(row, attack=True)
            record = await create_memory(
                session,
                tenant=tenant,
                provenance="log",
                type="observation",
                scope="asb_system_admin",
                subject=row.get("Attacker Tool", "unknown"),
                predicate="tool_description",
                content=text,
            )
            quarantined = record.status == "quarantined"
            if quarantined:
                tp += 1
            else:
                fn += 1
            detail.append({
                "tool": row.get("Attacker Tool", "unknown"),
                "expected": "quarantined",
                "actual": record.status,
                "reason": record.meta.get("quarantine_reason"),
                "aggressive": row.get("Aggressive"),
            })

        # Test normals (expected active).
        for row in normals:
            text = _memory_text(row, attack=False)
            record = await create_memory(
                session,
                tenant=tenant,
                provenance="log",
                type="observation",
                scope="asb_system_admin",
                subject=row.get("Tool Name", "unknown"),
                predicate="tool_description",
                content=text,
            )
            quarantined = record.status == "quarantined"
            if quarantined:
                fp += 1
            else:
                tn += 1
            detail.append({
                "tool": row.get("Tool Name", "unknown"),
                "expected": "active",
                "actual": record.status,
                "reason": record.meta.get("quarantine_reason"),
            })

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) else 0.0

    await engine.dispose()

    return {
        "total_attack": len(attacks),
        "total_normal": len(normals),
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "samples": detail[:20],  # first 20 for inspection
    }


def main() -> None:
    summary = asyncio.run(_run_asb(
        attack_files=["all_attack_tools.jsonl"],
        normal_files=["all_normal_tools.jsonl"],
        sample_attacks=100,
        sample_normals=20,
    ))
    out = Path(__file__).parent.parent.parent.parent / "evaluations" / "asb_memorygate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out}")
    print(json.dumps({k: summary[k] for k in summary if k != "samples"}, indent=2, default=str))


if __name__ == "__main__":
    main()
