from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest

from src.config import settings
from src.models import ChunkData, TextBlock
from src.pipeline import runner
from src.pipeline.versioner import VersionActivationResult


class Transaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


class PersistConnection:
    def __init__(self, total_chunks: int) -> None:
        self.total_chunks = total_chunks
        self.transaction_state = Transaction()
        self.executemany_calls = []
        self.execute_calls = []

    def transaction(self):
        return self.transaction_state

    async def executemany(self, query, arguments):
        self.executemany_calls.append((query, arguments))

    async def fetchrow(self, query, *arguments):
        assert "COUNT(*)" in query
        return {"total_chunks": self.total_chunks}

    async def execute(self, query, *arguments):
        self.execute_calls.append((query, arguments))
        return "UPDATE 1"


def install_connection(monkeypatch: pytest.MonkeyPatch, connection) -> None:
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(runner.db, "connection", fake_connection)


def _chunk(index: int = 0) -> ChunkData:
    return ChunkData(
        index=index,
        content=f"content-{index}",
        page_number=1,
        section_title="Section",
        char_start=index * 10,
        char_end=index * 10 + 9,
    )


@pytest.mark.asyncio
async def test_persist_chunks_is_atomic_and_records_version_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = uuid4()
    document_id = uuid4()
    connection = PersistConnection(total_chunks=2)
    install_connection(monkeypatch, connection)

    result = await runner._persist_chunks(
        version_id,
        document_id,
        [_chunk(0), _chunk(1)],
    )

    assert result == 2
    assert connection.transaction_state.committed is True
    assert len(connection.executemany_calls) == 1
    assert len(connection.executemany_calls[0][1]) == 2
    assert connection.execute_calls[0][1] == (version_id, 2, document_id)
    assert "ON CONFLICT (version_id, chunk_index) DO NOTHING" in connection.executemany_calls[0][0]


@pytest.mark.asyncio
async def test_persist_chunks_rolls_back_on_count_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = PersistConnection(total_chunks=1)
    install_connection(monkeypatch, connection)

    with pytest.raises(runner.PipelineError, match="does not match"):
        await runner._persist_chunks(uuid4(), uuid4(), [_chunk(0), _chunk(1)])

    assert connection.transaction_state.rolled_back is True
    assert connection.execute_calls == []


@pytest.mark.asyncio
async def test_embed_pending_chunks_batches_and_saves_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    version_id = uuid4()
    document_id = uuid4()
    rows = [{"id": uuid4(), "content": f"text-{index}"} for index in range(5)]
    batches = []
    saved_records = []

    async def fake_pending(*args):
        return rows

    async def fake_embeddings(texts):
        batches.append(texts)
        return [[0.1] * settings.EMBEDDING_DIMENSIONS for _ in texts]

    async def fake_save(version, document, records):
        saved_records.append((version, document, records))

    monkeypatch.setattr(runner, "_pending_chunks", fake_pending)
    monkeypatch.setattr(runner, "generate_embeddings", fake_embeddings)
    monkeypatch.setattr(runner, "save_embedding_batch", fake_save)

    result = await runner._embed_pending_chunks(version_id, document_id)

    assert result == 5
    assert batches == [["text-0", "text-1"], ["text-2", "text-3"], ["text-4"]]
    assert [len(item[2]) for item in saved_records] == [2, 2, 1]
    assert all(item[0] == version_id and item[1] == document_id for item in saved_records)


@pytest.mark.asyncio
async def test_process_document_job_runs_pipeline_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    version_id = uuid4()
    document_id = uuid4()
    source = tmp_path / f"{version_id}.md"
    source.write_text("# title\ncontent", encoding="utf-8")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    calls = []
    expected = VersionActivationResult(
        version_id=version_id,
        document_id=document_id,
        total_chunks=1,
        embedded_chunks=1,
    )

    async def fake_context(*args):
        return {
            "file_type": "markdown",
            "chunk_size": 512,
            "chunk_overlap": 50,
            "version_status": "PROCESSING",
        }

    def fake_extract(path, file_type):
        calls.append(("extract", path, file_type))
        return [TextBlock(text="content", section="title")]

    def fake_chunk(blocks, chunk_size, overlap):
        calls.append(("chunk", blocks, chunk_size, overlap))
        return [_chunk()]

    async def fake_persist(version, document, chunks):
        calls.append(("persist", version, document, chunks))
        return 1

    async def fake_embed(version, document):
        calls.append(("embed", version, document))
        return 1

    async def fake_activate(version):
        calls.append(("activate", version))
        return expected

    monkeypatch.setattr(runner, "_load_context", fake_context)
    monkeypatch.setattr(runner, "extract_text", fake_extract)
    monkeypatch.setattr(runner, "chunk_text", fake_chunk)
    monkeypatch.setattr(runner, "_persist_chunks", fake_persist)
    monkeypatch.setattr(runner, "_embed_pending_chunks", fake_embed)
    monkeypatch.setattr(runner, "activate_version", fake_activate)

    result = await runner.process_document_job(
        {"version_id": version_id, "document_id": document_id}
    )

    assert result == expected
    assert [call[0] for call in calls] == ["extract", "chunk", "persist", "embed", "activate"]
    assert calls[0][1] == str(source)


@pytest.mark.asyncio
async def test_process_document_job_reuses_already_active_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = uuid4()
    document_id = uuid4()
    expected = VersionActivationResult(
        version_id=version_id,
        document_id=document_id,
        total_chunks=1,
        embedded_chunks=1,
        already_active=True,
    )

    async def fake_context(*args):
        return {
            "file_type": "txt",
            "chunk_size": 512,
            "chunk_overlap": 50,
            "version_status": "ACTIVE",
        }

    async def fake_activate(version):
        assert version == version_id
        return expected

    def should_not_extract(*args):
        raise AssertionError("active version must not be reprocessed")

    monkeypatch.setattr(runner, "_load_context", fake_context)
    monkeypatch.setattr(runner, "activate_version", fake_activate)
    monkeypatch.setattr(runner, "extract_text", should_not_extract)

    result = await runner.process_document_job(
        {"version_id": version_id, "document_id": document_id}
    )

    assert result == expected
