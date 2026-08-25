from __future__ import annotations

import math
from typing import Any, List

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import settings


class EmbeddingError(RuntimeError):
    """Base exception for embedding client failures."""


class EmbeddingConnectionError(EmbeddingError):
    """Raised for retryable Ollama connectivity or service failures."""


class EmbeddingResponseError(EmbeddingError):
    """Raised when Ollama returns a permanent or invalid response."""


def _validate_texts(texts: List[str]) -> None:
    if not isinstance(texts, list):
        raise TypeError("texts must be a list of strings")

    for index, text in enumerate(texts):
        if not isinstance(text, str):
            raise TypeError(f"texts[{index}] must be a string")
        if not text.strip():
            raise ValueError(f"texts[{index}] must not be empty or whitespace")


def _validate_embeddings(
    payload: Any,
    expected_count: int,
) -> List[List[float]]:
    if not isinstance(payload, dict):
        raise EmbeddingResponseError("Ollama response must be a JSON object")

    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list):
        raise EmbeddingResponseError("Ollama response does not contain an embeddings list")

    if len(embeddings) != expected_count:
        raise EmbeddingResponseError(
            "Embedding count does not match input count: "
            f"expected {expected_count}, got {len(embeddings)}"
        )

    validated: List[List[float]] = []
    expected_dimensions = settings.EMBEDDING_DIMENSIONS

    for vector_index, vector in enumerate(embeddings):
        if not isinstance(vector, list):
            raise EmbeddingResponseError(f"embeddings[{vector_index}] must be a list")

        if len(vector) != expected_dimensions:
            raise EmbeddingResponseError(
                f"embeddings[{vector_index}] has invalid dimensions: "
                f"expected {expected_dimensions}, got {len(vector)}"
            )

        validated_vector: List[float] = []
        for value_index, value in enumerate(vector):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingResponseError(
                    f"embeddings[{vector_index}][{value_index}] " "must be a number"
                )

            float_value = float(value)
            if not math.isfinite(float_value):
                raise EmbeddingResponseError(
                    f"embeddings[{vector_index}][{value_index}] " "must be finite"
                )
            validated_vector.append(float_value)

        validated.append(validated_vector)

    return validated


def _create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.EMBEDDING_TIMEOUT_SECONDS)


async def _post_embedding_batch(
    client: httpx.AsyncClient,
    texts: List[str],
) -> List[List[float]]:
    endpoint = f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/api/embed"
    request_payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": texts,
        "dimensions": settings.EMBEDDING_DIMENSIONS,
        "truncate": False,
        "keep_alive": settings.EMBEDDING_KEEP_ALIVE,
    }

    try:
        response = await client.post(endpoint, json=request_payload)
    except httpx.TimeoutException as exc:
        raise EmbeddingConnectionError(
            "Timed out while connecting to the Ollama embedding service"
        ) from exc
    except httpx.RequestError as exc:
        raise EmbeddingConnectionError(
            "Network error while calling the Ollama embedding service"
        ) from exc

    if response.status_code >= 500:
        raise EmbeddingConnectionError(
            f"Ollama embedding service returned HTTP {response.status_code}"
        )

    if not 200 <= response.status_code < 300:
        raise EmbeddingResponseError(
            f"Ollama embedding request was rejected with " f"HTTP {response.status_code}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise EmbeddingResponseError("Ollama embedding service returned invalid JSON") from exc

    return _validate_embeddings(payload, expected_count=len(texts))


async def _request_embedding_batch(
    client: httpx.AsyncClient,
    texts: List[str],
) -> List[List[float]]:
    attempts = max(1, settings.MAX_RETRIES)
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(EmbeddingConnectionError),
        reraise=True,
    )

    async for attempt in retrying:
        with attempt:
            return await _post_embedding_batch(client, texts)

    raise EmbeddingConnectionError("Ollama embedding request failed without returning a result")


async def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate one 1024-dimensional embedding per input text through Ollama.

    Inputs are sent sequentially in configured batches. The returned list
    preserves input order. Retryable transport and service failures are
    retried within a batch; invalid inputs and invalid responses fail
    immediately.
    """
    _validate_texts(texts)
    if not texts:
        return []

    if settings.EMBEDDING_API_PROVIDER.lower() != "ollama":
        raise EmbeddingResponseError("EMBEDDING_API_PROVIDER must be set to 'ollama'")
    if settings.EMBEDDING_DIMENSIONS <= 0:
        raise ValueError("EMBEDDING_DIMENSIONS must be greater than zero")
    if settings.EMBEDDING_BATCH_SIZE <= 0:
        raise ValueError("EMBEDDING_BATCH_SIZE must be greater than zero")
    if settings.EMBEDDING_TIMEOUT_SECONDS <= 0:
        raise ValueError("EMBEDDING_TIMEOUT_SECONDS must be greater than zero")
    if not settings.EMBEDDING_BASE_URL.strip():
        raise ValueError("EMBEDDING_BASE_URL must not be empty")
    if not settings.EMBEDDING_MODEL.strip():
        raise ValueError("EMBEDDING_MODEL must not be empty")

    all_embeddings: List[List[float]] = []

    async with _create_http_client() as client:
        for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
            batch_embeddings = await _request_embedding_batch(client, batch)
            all_embeddings.extend(batch_embeddings)

    if len(all_embeddings) != len(texts):
        raise EmbeddingResponseError(
            "Final embedding count does not match input count: "
            f"expected {len(texts)}, got {len(all_embeddings)}"
        )

    return all_embeddings
