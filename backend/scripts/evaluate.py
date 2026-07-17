"""Run the stateless-vs-memory evaluation and write evaluations/latest.json.

Usage:
    source venv/bin/activate
    python backend/scripts/evaluate.py
"""
import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.config import settings
from backend.app.eval import run_evaluation
from backend.app.models import Base

SCENARIOS_PATH = Path(__file__).parent.parent / "evaluations" / "scenarios.json"
OUTPUT_PATH = Path(__file__).parent.parent.parent / "evaluations" / "latest.json"


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    scenarios = json.loads(SCENARIOS_PATH.read_text())
    async with SessionLocal() as session:
        summary = await run_evaluation(session, scenarios)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Stateless accuracy: {summary['stateless_accuracy']:.2%}")
    print(f"Memory accuracy: {summary['memory_accuracy']:.2%}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
