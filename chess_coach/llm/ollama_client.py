"""Thin async wrapper around the Ollama HTTP API.

Only the endpoints we actually use are implemented:

* ``/api/chat``       streaming or non-streaming chat completions
* ``/api/generate``   single-turn generate (used for structured JSON tasks)
* ``/api/embed``      batch embedding

All calls go through a single ``httpx.AsyncClient`` with retries on 5xx. The
client is cached per settings instance so the connection pool is reused.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, AsyncIterator, Iterable, Sequence

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import settings


ChatMessage = dict[str, str]


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or settings.ollama_timeout_s),
            headers={"User-Agent": settings.user_agent},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
        json_mode: bool = False,
        stop: Sequence[str] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or settings.ollama_gen_model,
            "messages": list(messages),
            "stream": False,
            "options": {
                "temperature": settings.ollama_gen_temperature if temperature is None else temperature,
                "num_ctx": num_ctx or settings.ollama_gen_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        if stop:
            payload["options"]["stop"] = list(stop)

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError, OllamaError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.post(f"{self.base_url}/api/chat", json=payload)
                if resp.status_code >= 500:
                    raise OllamaError(f"Ollama {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                msg = data.get("message", {}).get("content", "")
                if not msg:
                    raise OllamaError(f"Empty response: {data}")
                return msg
        raise OllamaError("unreachable")

    async def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": model or settings.ollama_gen_model,
            "messages": list(messages),
            "stream": True,
            "options": {
                "temperature": settings.ollama_gen_temperature if temperature is None else temperature,
                "num_ctx": settings.ollama_gen_num_ctx,
            },
        }
        async with self._client.stream(
            "POST", f"{self.base_url}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    async def generate_json(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Chat helper that forces JSON output and returns a parsed dict."""
        raw = await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=temperature,
            json_mode=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Model did not return valid JSON: {raw[:500]}") from exc

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    async def embed(
        self,
        texts: Sequence[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": model or settings.ollama_embed_model,
            "input": list(texts),
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=1, max=20),
            retry=retry_if_exception_type((httpx.HTTPError, OllamaError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.post(f"{self.base_url}/api/embed", json=payload)
                if resp.status_code >= 500:
                    raise OllamaError(f"Ollama {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                data = resp.json()
                vectors = data.get("embeddings")
                if not vectors or len(vectors) != len(texts):
                    raise OllamaError(f"Bad embed response: {data}")
                if len(vectors[0]) != settings.ollama_embed_dims:
                    raise OllamaError(
                        f"Embedding dim mismatch: got {len(vectors[0])}, "
                        f"expected {settings.ollama_embed_dims}"
                    )
                return vectors
        raise OllamaError("unreachable")

    async def embed_one(self, text: str, *, model: str | None = None) -> list[float]:
        return (await self.embed([text], model=model))[0]

    async def embed_batched(
        self,
        texts: Iterable[str],
        *,
        batch_size: int = 32,
        model: str | None = None,
    ) -> list[list[float]]:
        out: list[list[float]] = []
        batch: list[str] = []
        for t in texts:
            batch.append(t)
            if len(batch) >= batch_size:
                out.extend(await self.embed(batch, model=model))
                batch.clear()
        if batch:
            out.extend(await self.embed(batch, model=model))
        return out


@lru_cache(maxsize=1)
def get_client() -> OllamaClient:
    return OllamaClient()
