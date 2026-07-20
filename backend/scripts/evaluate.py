"""Run the stateless-vs-memory evaluation and write the mode-specific artifact.

Usage:
    source venv/bin/activate
    python backend/scripts/evaluate.py                 # deterministic mock regression
    python backend/scripts/evaluate.py --live          # canonical live Qwen evidence
"""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.config import settings
from backend.app.eval import run_evaluation
from backend.app.models import Base

BACKEND_DIR = Path(__file__).parent.parent
REPO_ROOT = BACKEND_DIR.parent
SCENARIOS_PATH = BACKEND_DIR / "evaluations" / "scenarios.json"
LIVE_OUTPUT_PATH = REPO_ROOT / "evaluations" / "latest.json"
MOCK_OUTPUT_PATH = BACKEND_DIR / "evaluations" / "mock_regression.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use real Qwen Cloud calls instead of MockQwen")
    parser.add_argument("--count", type=int, default=0, help="Limit number of scenarios (0 = all)")
    args = parser.parse_args()

    async def _run() -> None:
        engine = create_async_engine(settings.database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

        scenarios = json.loads(SCENARIOS_PATH.read_text())
        if args.count:
            scenarios = scenarios[: args.count]

        async with SessionLocal() as session:
            summary = await run_evaluation(session, scenarios, live=args.live)

        output_path = LIVE_OUTPUT_PATH if args.live else MOCK_OUTPUT_PATH
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, default=str))
        print(f"Wrote {output_path}")
        print(f"Stateless accuracy: {summary['stateless_accuracy']:.2%}")
        print(f"Memory accuracy: {summary['memory_accuracy']:.2%}")
        if args.live:
            print("(Live Qwen calls used)")

        await engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
