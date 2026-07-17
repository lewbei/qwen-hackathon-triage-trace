"""Smoke test: make the first real Qwen Cloud call.

Usage:
    source venv/bin/activate
    QWEN_API_KEY=... python backend/scripts/live_qwen_smoke.py
"""
import asyncio
import json

from backend.app.qwen import QwenGateway


async def main() -> None:
    gateway = QwenGateway()
    response = await gateway.chat(
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'Qwen is ready for TriageTrace' and return JSON {ready: true}."},
        ],
        temperature=0.0,
    )
    print(json.dumps(response, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
