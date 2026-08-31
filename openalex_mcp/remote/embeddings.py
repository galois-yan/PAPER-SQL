"""ZhipuAI Embedding-3 client — semantic vector embeddings via HTTP API.

Provides EmbeddingClient for calling the ZhipuAI embedding API.
Pure functions (truncate_text, cosine_similarity, etc.) live in local/embedding.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

# Embedding constants (ZhipuAI Embedding-3)
EMBEDDING_API_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
EMBEDDING_MODEL = "embedding-3"
EMBEDDING_DEFAULT_DIMS = 1024
EMBEDDING_BATCH_SIZE = 64  # API max per request
EMBEDDING_MAX_CHARS = 3000  # safe truncation under 3072-token limit

logger = logging.getLogger(__name__)

EMBEDDING_MAX_RETRIES = 4
EMBEDDING_RETRY_BACKOFF = 2.0  # seconds (fixed, simple)

# Transient network failures worth retrying (not 4xx auth errors).
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    return isinstance(exc, httpx.RequestError) and not isinstance(
        exc, httpx.HTTPStatusError
    )


class EmbeddingClient:
    """Async client for ZhipuAI Embedding-3 API.

    Args:
        api_key: ZhipuAI API key (from https://bigmodel.cn).
        model: Model name (default ``"embedding-3"``).
        dimensions: Vector dimensions (default 1024).
        max_chars: Truncate input text to this many chars (default 3000).
    """

    def __init__(
        self,
        api_key: str,
        model: str = EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DEFAULT_DIMS,
        max_chars: int = EMBEDDING_MAX_CHARS,
    ):
        if not api_key:
            raise ValueError("ZHIPUAI_API_KEY is required for EmbeddingClient")
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.max_chars = max_chars
        self._http = httpx.AsyncClient(
            base_url=EMBEDDING_API_URL,
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()

    async def embed(self, text: str) -> list[float]:
        """Get embedding vector for a single text string."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Get embedding vectors for multiple texts.

        Handles batching to respect the API's 64-text-per-request limit.
        """
        # Truncate inline (no dependency on local/embedding.py's truncate_text)
        truncated = [
            t[: self.max_chars] if len(t) > self.max_chars else t for t in texts
        ]

        all_embeddings: list[list[float]] = []

        for i in range(0, len(truncated), EMBEDDING_BATCH_SIZE):
            batch = truncated[i : i + EMBEDDING_BATCH_SIZE]
            batch_results = await self._embed_chunk(batch)
            all_embeddings.extend(batch_results)

        return all_embeddings

    async def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        """Send a single API request for up to 64 texts, with retry on
        transient network errors."""
        payload: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
        }

        last_exc: Exception | None = None
        for attempt in range(EMBEDDING_MAX_RETRIES):
            try:
                response = await self._http.post("", json=payload)
                response.raise_for_status()
                data = response.json()

                items = data.get("data", [])
                items.sort(key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in items]
            except httpx.HTTPStatusError:
                raise  # 4xx/5xx from the API — auth/quota errors, not transient
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                last_exc = exc
                if attempt < EMBEDDING_MAX_RETRIES - 1:
                    wait = EMBEDDING_RETRY_BACKOFF * (attempt + 1)
                    logger.warning(
                        "Embedding API network error (%s), retry %d/%d in %.0fs",
                        type(exc).__name__,
                        attempt + 1,
                        EMBEDDING_MAX_RETRIES,
                        wait,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"Embedding API failed after {EMBEDDING_MAX_RETRIES} retries: "
            f"{last_exc or 'unknown error'}"
        )
