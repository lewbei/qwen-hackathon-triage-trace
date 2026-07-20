"""Download the MemoryAgentBench Conflict_Resolution split for local benchmarking."""
from __future__ import annotations

import os
from pathlib import Path

from datasets import load_dataset


def main() -> None:
    token = os.environ.get("HF_TOKEN")
    out_dir = Path(__file__).parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "memoryagentbench_conflict_resolution.jsonl"

    ds = load_dataset(
        "ai-hyz/MemoryAgentBench",
        split="Conflict_Resolution",
        token=token,
        trust_remote_code=True,
    )
    ds.to_json(out_file)
    print(f"Wrote {out_file} ({len(ds)} samples, {out_file.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
