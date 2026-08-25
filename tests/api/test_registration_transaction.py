from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from src.api import routes
from src.api.routes import DuplicateUploadError, StagedUpload
from src.config import settings


class FakeTransaction:
    def __init__(self, fail_commit: bool = False) -> None:
        self.fail_commit = fail_commit
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.rolled_back = True
            return False
        if self.fail_commit:
            self.rolled_back = True
            raise RuntimeError("commit failed")
        self.committed = True
        return False


class FakeRegistrationConnection:
    def __init__(self, duplicate=False, fail_commit=False, existing=False) -> None:
        self.document_id = uuid4()
        self.version_id = uuid4()
        self.duplicate = duplicate
        self.existing = existing
        self.transaction_state = FakeTransaction(fail_commit=fail_commit)
        self.calls = []

    def transaction(self):
        return self.transaction_state

    async def execute(self, query, *arguments):
        self.calls.append(("execute", query, arguments))
        return "OK"

    async def fetchrow(self, query, *arguments):
        self.calls.append(("fetchrow", query, arguments))
        if "dv.file_hash" in query:
            if self.duplicate:
                return {"document_id": self.document_id, "version_id": self.version_id}
            return None
        if "FROM doc_search.documents" in query and "FOR UPDATE" in query:
            return {"id": self.document_id} if self.existing else None
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetchval(self, query, *arguments):
        self.calls.append(("fetchval", query, arguments))
        if "FROM doc_search.embedding_models" in query:
            return 11
        if "INSERT INTO doc_search.documents" in query:
            return self.document_id
        if "COALESCE(MAX(version_number)" in query:
            return 2
        if "INSERT INTO doc_search.document_versions" in query:
            return self.version_id
        raise AssertionError(f"unexpected fetchval: {query}")


def _staged_file(tmp_path: Path) -> StagedUpload:
    path = tmp_path / "upload-test.tmp"
    path.write_bytes(b"OpenSQL")
    return StagedUpload(
        path=path,
        file_type="txt",
        extension=".txt",
        file_size_bytes=7,
        file_hash="a" * 64,
    )


def _install_connection(monkeypatch, connection):
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(routes.db, "connection", fake_connection)


@pytest.mark.asyncio
async def test_registration_commits_three_table_transaction_and_moves_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    staged = _staged_file(tmp_path)
    connection = FakeRegistrationConnection()
    _install_connection(monkeypatch, connection)

    result = await routes._register_upload(
        staged=staged,
        title="문서",
        category="분류",
        tags=["태그"],
        uploader_id="tester",
        existing_document_id=None,
    )

    assert result.document_id == connection.document_id
    assert result.version_id == connection.version_id
    assert result.version_number == 1
    assert connection.transaction_state.committed is True
    assert not staged.path.exists()
    assert result.final_path.read_bytes() == b"OpenSQL"
    combined_sql = "\n".join(call[1] for call in connection.calls)
    assert "pg_advisory_xact_lock" in combined_sql
    assert "INSERT INTO doc_search.documents" in combined_sql
    assert "INSERT INTO doc_search.document_versions" in combined_sql
    assert "INSERT INTO doc_search.change_log" in combined_sql


@pytest.mark.asyncio
async def test_duplicate_rolls_back_without_moving_staged_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    staged = _staged_file(tmp_path)
    connection = FakeRegistrationConnection(duplicate=True)
    _install_connection(monkeypatch, connection)

    with pytest.raises(DuplicateUploadError):
        await routes._register_upload(
            staged=staged,
            title="문서",
            category=None,
            tags=[],
            uploader_id=None,
            existing_document_id=None,
        )

    assert connection.transaction_state.rolled_back is True
    assert staged.path.exists()
    assert list(tmp_path.glob(f"{connection.version_id}.*")) == []


@pytest.mark.asyncio
async def test_commit_failure_removes_final_file_as_compensation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    staged = _staged_file(tmp_path)
    connection = FakeRegistrationConnection(fail_commit=True)
    _install_connection(monkeypatch, connection)

    with pytest.raises(RuntimeError, match="commit failed"):
        await routes._register_upload(
            staged=staged,
            title="문서",
            category=None,
            tags=[],
            uploader_id=None,
            existing_document_id=None,
        )

    final_path = tmp_path / f"{connection.version_id}.txt"
    assert connection.transaction_state.rolled_back is True
    assert not final_path.exists()
    assert not staged.path.exists()


@pytest.mark.asyncio
async def test_existing_document_upload_creates_next_version_without_new_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    staged = _staged_file(tmp_path)
    connection = FakeRegistrationConnection(existing=True)
    _install_connection(monkeypatch, connection)

    result = await routes._register_upload(
        staged=staged,
        title="문서 V2",
        category="분류",
        tags=["태그"],
        uploader_id=None,
        existing_document_id=connection.document_id,
    )

    assert result.document_id == connection.document_id
    assert result.version_number == 2
    combined_sql = "\n".join(call[1] for call in connection.calls)
    assert "SELECT COALESCE(MAX(version_number), 0) + 1" in combined_sql
    assert "UPDATE doc_search.documents" in combined_sql
    assert "INSERT INTO doc_search.documents" not in combined_sql
