import json
import math
import os

import httpx
import pytest
from tenacity import wait_none

from src.config import settings
from src.embedding import client as embedding_client
from src.embedding.client import (
    EmbeddingConnectionError,
    EmbeddingResponseError,
    check_embedding_service,
    generate_embeddings,
)


def _vector(value: float = 0.1) -> list[float]:
    return [value] * settings.EMBEDDING_DIMENSIONS


def _mock_client(
    monkeypatch: pytest.MonkeyPatch,
    handler,
) -> None:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        embedding_client,
        "_create_http_client",
        lambda: httpx.AsyncClient(transport=transport),
    )


def _mock_health_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        embedding_client,
        "_create_health_client",
        lambda: httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_embedding_readiness_requires_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": settings.EMBEDDING_MODEL}]})

    _mock_health_client(monkeypatch, handler)
    await check_embedding_service()


@pytest.mark.asyncio
async def test_embedding_readiness_rejects_missing_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "different-model"}]})

    _mock_health_client(monkeypatch, handler)
    with pytest.raises(EmbeddingResponseError, match="not installed"):
        await check_embedding_service()


@pytest.mark.asyncio
async def test_empty_list_returns_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called():
        raise AssertionError("HTTP client must not be created for an empty list")

    monkeypatch.setattr(
        embedding_client,
        "_create_http_client",
        fail_if_called,
    )

    assert await generate_embeddings([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_texts",
    [None, "single string", 123, {"text": "value"}],
)
async def test_rejects_non_list_input(invalid_texts) -> None:
    with pytest.raises(TypeError):
        await generate_embeddings(invalid_texts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_texts, expected_error",
    [
        (["valid", None], TypeError),
        (["valid", 123], TypeError),
        ([""], ValueError),
        (["   "], ValueError),
    ],
)
async def test_rejects_invalid_list_items(
    invalid_texts,
    expected_error,
) -> None:
    with pytest.raises(expected_error):
        await generate_embeddings(invalid_texts)


@pytest.mark.asyncio
async def test_generates_embeddings_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/api/embed"
        assert payload["model"] == "qwen3-embedding:0.6b"
        assert payload["dimensions"] == 1024
        assert payload["truncate"] is False
        assert payload["input"] == ["first", "second"]
        return httpx.Response(
            200,
            json={"embeddings": [_vector(0.1), _vector(0.2)]},
        )

    _mock_client(monkeypatch, handler)

    result = await generate_embeddings(["first", "second"])

    assert len(result) == 2
    assert len(result[0]) == 1024
    assert len(result[1]) == 1024
    assert result[0][0] == pytest.approx(0.1)
    assert result[1][0] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_splits_inputs_into_configured_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    received_batches = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        received_batches.append(payload["input"])
        embeddings = [_vector(float(index + 1)) for index, _ in enumerate(payload["input"])]
        return httpx.Response(200, json={"embeddings": embeddings})

    _mock_client(monkeypatch, handler)

    result = await generate_embeddings(["a", "b", "c", "d", "e"])

    assert received_batches == [["a", "b"], ["c", "d"], ["e"]]
    assert len(result) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_payload",
    [
        {},
        {"embeddings": "not-a-list"},
        {"embeddings": []},
        {"embeddings": [[0.1] * 3]},
        {"embeddings": [[math.nan] * 1024]},
        {"embeddings": [[math.inf] * 1024]},
        {"embeddings": [[True] * 1024]},
    ],
)
async def test_rejects_invalid_embedding_responses(
    monkeypatch: pytest.MonkeyPatch,
    response_payload,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_payload)

    _mock_client(monkeypatch, handler)

    with pytest.raises(EmbeddingResponseError):
        await generate_embeddings(["test"])


@pytest.mark.asyncio
async def test_retries_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_RETRIES", 3)
    monkeypatch.setattr(
        embedding_client,
        "wait_exponential",
        lambda **kwargs: wait_none(),
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary failure", request=request)
        return httpx.Response(
            200,
            json={"embeddings": [_vector()]},
        )

    _mock_client(monkeypatch, handler)

    result = await generate_embeddings(["retry"])

    assert attempts == 3
    assert len(result) == 1


@pytest.mark.asyncio
async def test_retries_timeout_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_RETRIES", 2)
    monkeypatch.setattr(
        embedding_client,
        "wait_exponential",
        lambda **kwargs: wait_none(),
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("temporary timeout", request=request)
        return httpx.Response(
            200,
            json={"embeddings": [_vector()]},
        )

    _mock_client(monkeypatch, handler)

    result = await generate_embeddings(["retry timeout"])

    assert attempts == 2
    assert len(result) == 1


@pytest.mark.asyncio
async def test_retries_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_RETRIES", 3)
    monkeypatch.setattr(
        embedding_client,
        "wait_exponential",
        lambda **kwargs: wait_none(),
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"embeddings": [_vector()]},
        )

    _mock_client(monkeypatch, handler)

    result = await generate_embeddings(["retry"])

    assert attempts == 3
    assert len(result) == 1


@pytest.mark.asyncio
async def test_does_not_retry_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400)

    _mock_client(monkeypatch, handler)

    with pytest.raises(EmbeddingResponseError):
        await generate_embeddings(["bad request"])

    assert attempts == 1


@pytest.mark.asyncio
async def test_rejects_invalid_json_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )

    _mock_client(monkeypatch, handler)

    with pytest.raises(EmbeddingResponseError):
        await generate_embeddings(["invalid json"])

    assert attempts == 1


@pytest.mark.asyncio
async def test_raises_after_retry_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_RETRIES", 3)
    monkeypatch.setattr(
        embedding_client,
        "wait_exponential",
        lambda **kwargs: wait_none(),
    )
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    _mock_client(monkeypatch, handler)

    with pytest.raises(EmbeddingConnectionError):
        await generate_embeddings(["offline"])

    assert attempts == 3


@pytest.mark.asyncio
async def test_live_ollama_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("RUN_OLLAMA_INTEGRATION") != "1":
        pytest.skip("set RUN_OLLAMA_INTEGRATION=1 to run")

    monkeypatch.setattr(
        settings,
        "EMBEDDING_BASE_URL",
        os.environ.get(
            "OLLAMA_TEST_BASE_URL",
            "http://127.0.0.1:11434",
        ),
    )

    result = await generate_embeddings(["OpenSQL 임베딩 클라이언트 통합 테스트"])

    assert len(result) == 1
    assert len(result[0]) == 1024
    assert all(math.isfinite(value) for value in result[0])
