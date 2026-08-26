from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.pipeline import versioner


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
    def __init__(self, version_row, completeness_row) -> None:
        self.version_row = version_row
        self.completeness_row = completeness_row
        self.transaction_state = Transaction()
        self.fetchrow_calls = []
        self.execute_calls = []

    def transaction(self):
        return self.transaction_state

    async def fetchrow(self, query, *arguments):
        self.fetchrow_calls.append((query, arguments))
        if "FOR UPDATE" in query:
            return self.version_row
        return self.completeness_row

    async def execute(self, query, *arguments):
        self.execute_calls.append((query, arguments))
        return "SELECT 1"


def install_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(versioner.db, "connection", fake_connection)


@pytest.mark.asyncio
async def test_activate_version_revalidates_counts_and_calls_stored_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = uuid4()
    document_id = uuid4()
    connection = FakeConnection(
        {"document_id": document_id, "status": "PROCESSING"},
        {
            "total_chunks": 3,
            "embedded_chunks": 3,
            "missing_embeddings": 0,
            "unmarked_chunks": 0,
        },
    )
    install_connection(monkeypatch, connection)

    result = await versioner.activate_version(version_id)

    assert result.version_id == version_id
    assert result.document_id == document_id
    assert result.total_chunks == 3
    assert result.embedded_chunks == 3
    assert result.already_active is False
    assert connection.transaction_state.committed is True
    assert connection.execute_calls[0][1] == (version_id,)
    assert "activate_version" in connection.execute_calls[0][0]


@pytest.mark.asyncio
async def test_activate_version_rejects_incomplete_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(
        {"document_id": uuid4(), "status": "PROCESSING"},
        {
            "total_chunks": 3,
            "embedded_chunks": 2,
            "missing_embeddings": 1,
            "unmarked_chunks": 0,
        },
    )
    install_connection(monkeypatch, connection)

    with pytest.raises(versioner.VersionNotReadyError, match="total=3"):
        await versioner.activate_version(uuid4())

    assert connection.transaction_state.rolled_back is True
    assert connection.execute_calls == []


@pytest.mark.asyncio
async def test_activate_version_is_idempotent_when_already_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = uuid4()
    connection = FakeConnection(
        {"document_id": uuid4(), "status": "ACTIVE"},
        {
            "total_chunks": 2,
            "embedded_chunks": 2,
            "missing_embeddings": 0,
            "unmarked_chunks": 0,
        },
    )
    install_connection(monkeypatch, connection)

    result = await versioner.activate_version(version_id)

    assert result.already_active is True
    assert connection.execute_calls == []
    assert connection.transaction_state.committed is True


@pytest.mark.asyncio
async def test_activate_version_rejects_missing_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(None, None)
    install_connection(monkeypatch, connection)

    with pytest.raises(versioner.VersionNotFoundError):
        await versioner.activate_version(uuid4())

    assert connection.transaction_state.rolled_back is True
