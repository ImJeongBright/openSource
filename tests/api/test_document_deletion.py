from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.api import routes
from src.config import settings


class FakeTransaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False


class FakeDeleteConnection:
    def __init__(self, document_id: UUID, version_ids: tuple[UUID, ...], busy=False) -> None:
        self.document_id = document_id
        self.version_ids = version_ids
        self.busy = busy
        self.transaction_state = FakeTransaction()
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []

    def transaction(self):
        return self.transaction_state

    async def fetchrow(self, query: str, *arguments):
        self.calls.append(("fetchrow", query, arguments))
        return {"id": self.document_id}

    async def fetch(self, query: str, *arguments):
        self.calls.append(("fetch", query, arguments))
        return [{"id": version_id} for version_id in self.version_ids]

    async def fetchval(self, query: str, *arguments):
        self.calls.append(("fetchval", query, arguments))
        return self.busy

    async def execute(self, query: str, *arguments):
        self.calls.append(("execute", query, arguments))
        if "DELETE FROM doc_search.documents" in query:
            return "DELETE 1"
        return "OK"


def _install_connection(monkeypatch: pytest.MonkeyPatch, connection) -> None:
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(routes.db, "connection", fake_connection)


@pytest.mark.asyncio
async def test_delete_is_atomic_records_event_and_removes_version_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    version_ids = (uuid4(), uuid4())
    connection = FakeDeleteConnection(document_id, version_ids)
    _install_connection(monkeypatch, connection)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

    for version_id in version_ids:
        (tmp_path / f"{version_id}.txt").write_text("source", encoding="utf-8")
    unrelated = tmp_path / f"{version_ids[0]}.log"
    unrelated.write_text("keep", encoding="utf-8")

    result = await routes._delete_document(document_id)

    assert connection.transaction_state.committed is True
    assert result.version_ids == version_ids
    assert result.deleted_files == 2
    assert unrelated.exists()
    combined_sql = "\n".join(call[1] for call in connection.calls)
    assert "status = 'DEAD_LETTER'" in combined_sql
    assert "event_type, status, document_id" in combined_sql
    assert "VALUES ('DELETE', 'COMPLETED'" in combined_sql
    assert "DELETE FROM doc_search.documents" in combined_sql


@pytest.mark.asyncio
async def test_delete_rolls_back_when_worker_is_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    version_id = uuid4()
    connection = FakeDeleteConnection(document_id, (version_id,), busy=True)
    _install_connection(monkeypatch, connection)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    source = tmp_path / f"{version_id}.md"
    source.write_text("source", encoding="utf-8")

    with pytest.raises(routes.DocumentBusyError):
        await routes._delete_document(document_id)

    assert connection.transaction_state.rolled_back is True
    assert source.exists()
    combined_sql = "\n".join(call[1] for call in connection.calls)
    assert "DELETE FROM doc_search.documents" not in combined_sql
