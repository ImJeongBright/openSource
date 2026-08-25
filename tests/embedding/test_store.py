from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.config import settings
from src.embedding import store
from src.embedding.store import EmbeddingBatchError, save_embedding_batch
from src.models import EmbeddingRecord


def _record(chunk_id=None, value: float = 0.1) -> EmbeddingRecord:
    return EmbeddingRecord(
        chunk_id=chunk_id or uuid4(),
        vector=[value] * settings.EMBEDDING_DIMENSIONS,
    )


class Transaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


class FakeConnection:
    def __init__(self, records, inserted_count=None, embedded_count=None) -> None:
        self.records = records
        self.inserted_count = len(records) if inserted_count is None else inserted_count
        self.embedded_count = len(records) if embedded_count is None else embedded_count
        self.transaction_state = Transaction()
        self.execute_calls = []
        self.insert_arguments = None

    def transaction(self):
        return self.transaction_state

    async def fetchrow(self, query, *arguments):
        assert "document_versions" in query
        return {"id": arguments[0], "embedding_model_id": 7}

    async def fetch(self, query, *arguments):
        if "FROM doc_search.chunks" in query:
            return [{"id": record.chunk_id} for record in self.records]
        if "INSERT INTO doc_search.embeddings" in query:
            self.insert_arguments = arguments
            return [{"chunk_id": record.chunk_id} for record in self.records[: self.inserted_count]]
        raise AssertionError(f"unexpected fetch query: {query}")

    async def fetchval(self, query, *arguments):
        assert "COUNT(*)" in query
        return self.embedded_count

    async def execute(self, query, *arguments):
        self.execute_calls.append((query, arguments))
        if "UPDATE doc_search.document_versions" in query:
            return "UPDATE 1"
        return "UPDATE 1"


def _install_connection(monkeypatch, connection):
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(store.db, "connection", fake_connection)


@pytest.mark.asyncio
async def test_empty_batch_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_connection():
        raise AssertionError("DB must not be opened for an empty batch")

    monkeypatch.setattr(store.db, "connection", fail_connection)
    result = await save_embedding_batch(uuid4(), uuid4(), [])
    assert result.model_dump() == {
        "requested_count": 0,
        "inserted_count": 0,
        "embedded_count": 0,
    }


def test_rejects_wrong_dimension() -> None:
    record = EmbeddingRecord(chunk_id=uuid4(), vector=[0.1, 0.2])
    with pytest.raises(EmbeddingBatchError, match="dimensions"):
        store._validate_records([record])


def test_rejects_duplicate_chunk_ids() -> None:
    chunk_id = uuid4()
    with pytest.raises(EmbeddingBatchError, match="duplicate chunk_id"):
        store._validate_records([_record(chunk_id), _record(chunk_id)])


def test_rejects_batch_over_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 1)
    with pytest.raises(EmbeddingBatchError, match="exceeds"):
        store._validate_records([_record(), _record()])


@pytest.mark.asyncio
async def test_saves_batch_and_recomputes_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record(), _record()]
    connection = FakeConnection(records, inserted_count=2, embedded_count=5)
    _install_connection(monkeypatch, connection)

    result = await save_embedding_batch(uuid4(), uuid4(), records)

    assert result.requested_count == 2
    assert result.inserted_count == 2
    assert result.embedded_count == 5
    assert connection.transaction_state.committed is True
    assert connection.insert_arguments is not None
    assert connection.insert_arguments[0] == [record.chunk_id for record in records]
    assert all(value.startswith("[") for value in connection.insert_arguments[1])
    assert any("is_embedded = TRUE" in call[0] for call in connection.execute_calls)


@pytest.mark.asyncio
async def test_retry_is_idempotent_and_does_not_increment_requested_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [_record(), _record()]
    connection = FakeConnection(records, inserted_count=0, embedded_count=2)
    _install_connection(monkeypatch, connection)

    result = await save_embedding_batch(uuid4(), uuid4(), records)

    assert result.inserted_count == 0
    assert result.embedded_count == 2
    assert connection.transaction_state.committed is True


@pytest.mark.asyncio
async def test_missing_chunk_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record(), _record()]
    connection = FakeConnection(records)

    original_fetch = connection.fetch

    async def missing_fetch(query, *arguments):
        if "FROM doc_search.chunks" in query:
            return [{"id": records[0].chunk_id}]
        return await original_fetch(query, *arguments)

    connection.fetch = missing_fetch
    _install_connection(monkeypatch, connection)

    with pytest.raises(EmbeddingBatchError, match="do not belong"):
        await save_embedding_batch(uuid4(), uuid4(), records)

    assert connection.transaction_state.rolled_back is True


@pytest.mark.asyncio
async def test_insert_error_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record()]
    connection = FakeConnection(records)
    original_fetch = connection.fetch

    async def failing_fetch(query, *arguments):
        if "INSERT INTO doc_search.embeddings" in query:
            raise RuntimeError("database disconnected")
        return await original_fetch(query, *arguments)

    connection.fetch = failing_fetch
    _install_connection(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="disconnected"):
        await save_embedding_batch(uuid4(), uuid4(), records)

    assert connection.transaction_state.rolled_back is True
