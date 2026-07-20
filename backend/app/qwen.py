from __future__ import annotations

import json
import time
from typing import Any

import httpx
from openai import AsyncOpenAI

from backend.app.config import settings


class QwenGateway:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (
            api_key
            or settings.qwen_api_key
            or settings.qwen_chat_api_key
            or settings.qwen_embedding_api_key
            or "sk-dummy"
        )
        chat_key = settings.qwen_chat_api_key or self._api_key
        chat_base = settings.qwen_chat_base_url or settings.qwen_base_url
        embed_key = settings.qwen_embedding_api_key or self._api_key
        embed_base = settings.qwen_embedding_base_url or settings.qwen_base_url
        self.chat_client = AsyncOpenAI(api_key=chat_key, base_url=chat_base, timeout=60.0)
        self.embedding_client = AsyncOpenAI(api_key=embed_key, base_url=embed_base, timeout=60.0)

    def _ensure_key(self) -> None:
        if not self._api_key or self._api_key == "sk-dummy":
            raise RuntimeError("QWEN_API_KEY is not configured")

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        self._ensure_key()
        start = time.perf_counter()
        response = await self.chat_client.chat.completions.create(
            model=model or settings.qwen_reasoning_model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = (time.perf_counter() - start) * 1000
        message = response.choices[0].message
        usage = response.usage
        return {
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in (message.tool_calls or [])
            ],
            "model": response.model,
            "token_usage": {
                "prompt": usage.prompt_tokens if usage else 0,
                "completion": usage.completion_tokens if usage else 0,
                "total": usage.total_tokens if usage else 0,
            },
            "latency_ms": latency_ms,
        }

    async def extract_structured(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        response = await self.chat(
            messages=messages,
            model=model or settings.qwen_extraction_model,
            temperature=0.0,
            max_tokens=2048,
        )
        text = response["content"] or "{}"
        # Qwen may wrap JSON in markdown fences.
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[-2] if text.count("```") >= 2 else text.strip("`")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Attempt to extract first JSON object.
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise

    async def embed(self, texts: list[str], dimensions: int = 1536) -> list[list[float]]:
        self._ensure_key()
        from openai import APIError

        try:
            response = await self.embedding_client.embeddings.create(
                input=texts,
                model=settings.qwen_embedding_model,
                dimensions=dimensions,
            )
        except APIError as exc:
            # Fall back to the configured fallback model if the primary one is unavailable or out of quota.
            msg = str(exc).lower()
            model_errors = ("not found", "not supported", "does not exist")
            quota_errors = ("out of quota", "quota exceeded", "insufficient quota", "insufficient_quota")
            if (
                exc.code in ("model_not_found", "invalid_model", "insufficient_quota")
                or any(phrase in msg for phrase in model_errors + quota_errors)
            ):
                # Some fallback models (e.g. text-embedding-v3) cap dimensions at 1024.
                fallback_dims = min(
                    dimensions,
                    settings.qwen_embedding_fallback_dimensions,
                )
                response = await self.embedding_client.embeddings.create(
                    input=texts,
                    model=settings.qwen_embedding_fallback_model,
                    dimensions=fallback_dims,
                )
            else:
                raise

        # Pad shorter fallback vectors with zeros so the DB dimension stays constant.
        return [
            item.embedding + [0.0] * (dimensions - len(item.embedding))
            for item in response.data
        ]

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call Qwen Cloud qwen3-rerank. Returns [{index, relevance_score}, ...] sorted by score."""
        self._ensure_key()
        top_n = top_n or len(documents)
        payload = {
            "model": model or settings.qwen_rerank_model,
            "input": {
                "query": query,
                "documents": documents,
            },
            "top_n": top_n,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                settings.qwen_rerank_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("output", {}).get("results", [])
            return sorted(results, key=lambda r: r["index"])


qwen = QwenGateway()
