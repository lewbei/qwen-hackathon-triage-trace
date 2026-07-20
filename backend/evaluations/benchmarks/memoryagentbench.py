"""Run TriageTrace's memory retrieval on MemoryAgentBench Conflict_Resolution.

MemoryAgentBench: https://huggingface.co/datasets/ai-hyz/MemoryAgentBench
Paper: Hu et al., "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions".

This runner tests the memory firewall's ability to retrieve and rank the
right facts for multi-hop questions, including temporal conflicts where newer
facts should override older ones.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.config import settings
from backend.app.memory import create_memory, search_memories
from backend.app.models import Base, MemoryRecord
from backend.app.qwen import qwen


def _parse_facts(context: str) -> list[str]:
    """Extract numbered facts from a MemoryAgentBench context."""
    facts: list[str] = []
    for line in context.splitlines():
        line = line.strip()
        m = re.match(r"^\d+\.\s+(.*)$", line)
        if m:
            facts.append(m.group(1).strip())
    return facts


def _subject_predicate(fact: str) -> tuple[str, str]:
    """Map a MemoryAgentBench fact to a semantic subject and predicate.

    Conflicting facts about the same entity share the same subject/predicate,
    so TriageTrace's temporal supersession keeps only the most recent one active.
    """
    fact = fact.strip()
    # Patterns are ordered from most specific to least specific.
    patterns: list[tuple[str, str]] = [
        (r"^The type of music that (.+?) plays is .+$", "music_type"),
        (r"^The company that produced (.+?) is .+$", "producer"),
        (r"^The origianl broadcaster of (.+?) is .+$", "original_broadcaster"),
        (r"^The original broadcaster of (.+?) is .+$", "original_broadcaster"),
        (r"^The univeristy where (.+?) was educated is .+$", "educated_at"),
        (r"^The university where (.+?) was educated is .+$", "educated_at"),
        (r"^The name of the current head of the (.+?) government is .+$", "head_of_government"),
        (r"^The name of the current head of state in (.+?) is .+$", "head_of_state"),
        (r"^The headquarters of (.+?) is located in the city of .+$", "headquarters_city"),
        (r"^The chief executive officer of (.+?) is .+$", "ceo"),
        (r"^The chairperson of (.+?) is .+$", "chairperson"),
        (r"^The Prime Minister of (.+?) is .+$", "prime_minister"),
        (r"^The official language of (.+?) is .+$", "official_language"),
        (r"^The capital of (.+?) is .+$", "capital"),
        (r"^The director of (.+?) is .+$", "director"),
        (r"^The author of (.+?) is .+$", "author"),
        (r"^(.+?)'s child is .+$", "child"),
        (r"^(.+?) works in the field of .+$", "field"),
        (r"^(.+?) worked in the city of .+$", "work_city"),
        (r"^(.+?) is employed by .+$", "employed_by"),
        (r"^(.+?) was written in the language of .+$", "written_language"),
        (r"^(.+?) was developed by .+$", "developed_by"),
        (r"^(.+?) was performed by .+$", "performed_by"),
        (r"^(.+?) is famous for .+$", "famous_for"),
        (r"^(.+?) is affiliated with the religion of .+$", "religion"),
        (r"^(.+?) speaks the language of .+$", "language"),
        (r"^(.+?) was created in the country of .+$", "created_in_country"),
        (r"^(.+?) was founded in the city of .+$", "founded_in_city"),
        (r"^(.+?) was founded by .+$", "founded_by"),
        (r"^(.+?) is located in the continent of .+$", "continent"),
        (r"^(.+?) plays the position of .+$", "position"),
        (r"^(.+?) is associated with the sport of .+$", "sport"),
        (r"^(.+?) is a citizen of .+$", "country_of_citizenship"),
        (r"^(.+?) is married to .+$", "married_to"),
        (r"^(.+?) died in the city of .+$", "died_in_city"),
        (r"^(.+?) was born in the city of .+$", "born_in_city"),
    ]
    for pattern, predicate in patterns:
        m = re.match(pattern, fact, flags=re.IGNORECASE)
        if m:
            subject = m.group(1).strip()
            if subject:
                return subject, predicate
    return f"fact_{uuid.uuid4().hex[:8]}", "statement"


def _extract_final_answer(prediction: str) -> str:
    """Pull the final answer from a CoT-style response, case-insensitively."""
    # Split on "Final Answer:" regardless of case and line breaks.
    match = re.split(r"final answer[:：]", prediction, flags=re.IGNORECASE, maxsplit=1)
    if len(match) > 1:
        return match[-1].strip().split("\n")[0].strip()
    if "</reasoning>" in prediction.lower():
        return prediction.split("</reasoning>", 1)[-1].strip().split("\n")[0].strip()
    return prediction.strip().split("\n")[0].strip()


def _normalize_answer(text: str) -> str:
    """Strip articles, punctuation, and extra whitespace for fuzzy matching."""
    text = text.lower().strip()
    text = re.sub(r"\b(the|a|an)\b", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def _answer_match(prediction: str, answers: list[str]) -> bool:
    pred = _normalize_answer(_extract_final_answer(prediction))
    if not pred:
        return False
    for ans in answers:
        a = _normalize_answer(ans)
        if a and (a == pred or a in pred or pred in a):
            return True
    return False


async def _run_mab(
    split: str = "Conflict_Resolution",
    max_samples: int = 3,
    max_questions_per_sample: int | None = 5,
) -> dict[str, Any]:
    from datasets import load_dataset

    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    scope = "general"

    local_path = os.environ.get("MEMORYAGENTBENCH_DATA")
    if local_path and Path(local_path).exists():
        ds = load_dataset("json", data_files=local_path, split="train", streaming=True)
    else:
        ds = load_dataset("ai-hyz/MemoryAgentBench", split=split, streaming=True)
    samples: list[dict[str, Any]] = []
    for i, sample in enumerate(ds):
        if i >= max_samples:
            break
        samples.append(sample)

    print(f"[mab] loaded {len(samples)} samples", flush=True)

    total = correct = 0
    details: list[dict[str, Any]] = []

    async with SessionLocal() as session:
        for sample_idx, sample in enumerate(samples):
            print(f"[mab] sample {sample_idx + 1}/{len(samples)}: {len(_parse_facts(sample['context']))} facts", flush=True)
            # Isolate each sample so earlier samples do not contaminate later ones.
            tenant = f"mab_eval_{uuid.uuid4().hex[:8]}_{sample_idx}"
            await session.execute(delete(MemoryRecord).where(MemoryRecord.tenant == tenant))
            await session.commit()

            facts = _parse_facts(sample["context"])
            base_ts = datetime.now(timezone.utc) - timedelta(minutes=len(facts))
            # Batch-embed facts in chunks of 10 (text-embedding-v4 limit).
            embeddings: list[list[float]] = []
            for i in range(0, len(facts), 10):
                chunk = facts[i : i + 10]
                embeddings.extend(await qwen.embed(chunk, dimensions=1536))
                if i and i % 50 == 0:
                    print(f"[mab] embedded {i}/{len(facts)} facts", flush=True)
            print(f"[mab] embedded {len(facts)} facts; inserting", flush=True)
            # Insert facts as memories; increasing source_timestamp models arrival order.
            for idx, (fact, emb) in enumerate(zip(facts, embeddings)):
                subject, predicate = _subject_predicate(fact)
                ts = base_ts + timedelta(seconds=idx)
                await create_memory(
                    session,
                    tenant=tenant,
                    provenance="log",
                    type="fact",
                    scope=scope,
                    subject=subject,
                    predicate=predicate,
                    content=fact,
                    source_authority=50,
                    source_timestamp=ts,
                    embedding=emb,
                )
            print(f"[mab] inserted {len(facts)} memories, asking questions", flush=True)

            questions = sample["questions"]
            answers = sample["answers"]
            if max_questions_per_sample:
                questions = questions[:max_questions_per_sample]
                answers = answers[:max_questions_per_sample]

            for q_idx, (q, ans_list) in enumerate(zip(questions, answers)):
                print(f"[mab] sample {sample_idx + 1} question {q_idx + 1}/{len(questions)}", flush=True)
                # Embed the question and retrieve active facts in this scope.
                query_emb = (await qwen.embed([q], dimensions=1536))[0]
                memories = await search_memories(
                    session,
                    tenant=tenant,
                    scope=scope,
                    query_embedding=query_emb,
                    limit=1000,
                )
                # Present facts in chronological order so the model can identify the
                # most recent one; include the source timestamp to make recency explicit.
                memories.sort(key=lambda m: m.source_timestamp or datetime.min.replace(tzinfo=timezone.utc))
                context_lines = [
                    f"{i+1}. [{m.source_timestamp.isoformat() if m.source_timestamp else 'unknown'}] {m.content}"
                    for i, m in enumerate(memories)
                ]
                prompt = (
                    "You are answering a multi-hop question using ONLY the provided facts.\n"
                    "Do NOT use any outside knowledge; these facts are the only source of truth.\n"
                    "Facts are listed in chronological order (larger index = newer).\n"
                    "If the facts conflict, trust the newest (highest-index) fact.\n"
                    "Reason step by step inside <reasoning> tags.\n"
                    "Then provide the final answer on a single line after 'Final Answer:'.\n\n"
                    "Facts:\n" + "\n".join(context_lines) + "\n\n"
                    f"Question: {q}\nAnswer:"
                )
                response = await qwen.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=settings.qwen_reasoning_model,
                    temperature=0.0,
                    max_tokens=256,
                )
                prediction = response.get("content") or ""
                matched = _answer_match(prediction, ans_list)
                if matched:
                    correct += 1
                total += 1
                details.append({
                    "question": q,
                    "prediction": prediction,
                    "answers": ans_list,
                    "matched": matched,
                    "recalled": len(context_lines),
                })
            print(f"[mab] sample {sample_idx + 1} done: {correct}/{total} correct so far", flush=True)

    await engine.dispose()

    return {
        "split": split,
        "samples": len(samples),
        "questions": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "details": details,
    }


def main() -> None:
    summary = asyncio.run(_run_mab())
    out = Path(__file__).parent.parent.parent.parent / "evaluations" / "memoryagentbench.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {out}", flush=True)
    print(f"Accuracy: {summary['accuracy']:.2%} ({summary['correct']}/{summary['questions']})", flush=True)


if __name__ == "__main__":
    main()
