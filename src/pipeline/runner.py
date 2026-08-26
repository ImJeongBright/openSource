from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List, Mapping, Sequence
from uuid import UUID

from src.config import settings
from src.db import db
from src.embedding.client import generate_embeddings
from src.embedding.store import save_embedding_batch
from src.models import ChunkData, EmbeddingRecord
from src.pipeline.chunker import chunk_text
from src.pipeline.extractor import extract_text
from src.pipeline.versioner import VersionActivationResult, activate_version


class PipelineError(RuntimeError):
    """Raised when a document cannot complete the Phase 7 pipeline."""


DOCUMENT_VERSION_CONTEXT_SQL = """
SELECT
    d.file_type,
    dv.chunk_size,
    dv.chunk_overlap,
    dv.status::text AS version_status
FROM doc_search.document_versions dv
JOIN doc_search.documents d ON d.id = dv.document_id
WHERE dv.id = $1 AND dv.document_id = $2
"""

INSERT_CHUNKS_SQL = """
INSERT INTO doc_search.chunks (
    version_id,
    document_id,
    chunk_index,
    content,
    page_number,
    section_title,
    char_start,
    char_end
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (version_id, chunk_index) DO NOTHING
"""

COUNT_CHUNKS_SQL = """
SELECT COUNT(*)::integer AS total_chunks
FROM doc_search.chunks
WHERE version_id = $1 AND document_id = $2
"""

UPDATE_TOTAL_CHUNKS_SQL = """
UPDATE doc_search.document_versions
SET total_chunks = $2, updated_at = NOW()
WHERE id = $1 AND document_id = $3 AND status = 'PROCESSING'
"""

PENDING_CHUNKS_SQL = """
SELECT c.id, c.content
FROM doc_search.chunks c
LEFT JOIN doc_search.embeddings e
    ON e.chunk_id = c.id
   AND e.version_id = c.version_id
   AND e.document_id = c.document_id
WHERE c.version_id = $1
  AND c.document_id = $2
  AND (NOT c.is_embedded OR e.chunk_id IS NULL)
ORDER BY c.chunk_index ASC
"""


def _required_uuid(job: Mapping[str, Any], key: str) -> UUID:
    value = job.get(key)
    if value is None:
        raise PipelineError(f"job is missing {key}")
    return value if isinstance(value, UUID) else UUID(str(value))


def _source_path(version_id: UUID, file_type: str) -> Path:
    extensions = {"pdf": ".pdf", "txt": ".txt", "markdown": ".md"}
    try:
        extension = extensions[file_type]
    except KeyError as exc:
        raise PipelineError(f"unsupported document file type: {file_type}") from exc
    return Path(settings.UPLOAD_DIR).resolve() / f"{version_id}{extension}"


async def _load_context(version_id: UUID, document_id: UUID) -> Mapping[str, Any]:
    async with db.connection() as connection:
        row = await connection.fetchrow(
            DOCUMENT_VERSION_CONTEXT_SQL,
            version_id,
            document_id,
        )
    if row is None:
        raise PipelineError(f"document version not found: {version_id}")
    if str(row["version_status"]) not in {"PROCESSING", "ACTIVE"}:
        raise PipelineError(
            f"document version {version_id} is not PROCESSING: {row['version_status']}"
        )
    return row


async def _persist_chunks(
    version_id: UUID,
    document_id: UUID,
    chunks: Sequence[ChunkData],
) -> int:
    if not chunks:
        raise PipelineError("document produced no searchable text chunks")

    values = [
        (
            version_id,
            document_id,
            chunk.index,
            chunk.content,
            chunk.page_number,
            chunk.section_title,
            chunk.char_start,
            chunk.char_end,
        )
        for chunk in chunks
    ]

    async with db.connection() as connection:
        async with connection.transaction():
            await connection.executemany(INSERT_CHUNKS_SQL, values)
            count_row = await connection.fetchrow(
                COUNT_CHUNKS_SQL,
                version_id,
                document_id,
            )
            total_chunks = int(count_row["total_chunks"])
            if total_chunks != len(chunks):
                raise PipelineError(
                    "persisted chunk count does not match extracted chunk count: "
                    f"expected {len(chunks)}, got {total_chunks}"
                )
            result = await connection.execute(
                UPDATE_TOTAL_CHUNKS_SQL,
                version_id,
                total_chunks,
                document_id,
            )
            if result != "UPDATE 1":
                raise PipelineError(f"failed to update total_chunks for version {version_id}")
    return total_chunks


async def _pending_chunks(version_id: UUID, document_id: UUID) -> List[Mapping[str, Any]]:
    async with db.connection() as connection:
        rows = await connection.fetch(
            PENDING_CHUNKS_SQL,
            version_id,
            document_id,
        )
    return list(rows)


async def _embed_pending_chunks(version_id: UUID, document_id: UUID) -> int:
    rows = await _pending_chunks(version_id, document_id)
    batch_size = max(1, int(settings.EMBEDDING_BATCH_SIZE))

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = await generate_embeddings([str(row["content"]) for row in batch])
        if len(vectors) != len(batch):
            raise PipelineError(
                "embedding response count does not match pending chunk count: "
                f"expected {len(batch)}, got {len(vectors)}"
            )
        records = [
            EmbeddingRecord(chunk_id=row["id"], vector=vector)
            for row, vector in zip(batch, vectors, strict=True)
        ]
        await save_embedding_batch(version_id, document_id, records)

    return len(rows)


async def process_document_job(job: Mapping[str, Any]) -> VersionActivationResult:
    """Run one claimed upload/update job through the complete Phase 7 pipeline."""
    version_id = _required_uuid(job, "version_id")
    document_id = _required_uuid(job, "document_id")
    context = await _load_context(version_id, document_id)
    if str(context["version_status"]) == "ACTIVE":
        # A previous attempt may have activated the version before losing its
        # change_log lease. Activation is idempotent, so finish without
        # re-reading or re-embedding the source file.
        return await activate_version(version_id)

    source_path = _source_path(version_id, str(context["file_type"]))
    if not source_path.is_file():
        raise PipelineError(f"uploaded source file does not exist: {source_path}")

    blocks = await asyncio.to_thread(
        extract_text,
        str(source_path),
        str(context["file_type"]),
    )
    chunks = await asyncio.to_thread(
        chunk_text,
        blocks,
        int(context["chunk_size"]),
        int(context["chunk_overlap"]),
    )
    await _persist_chunks(version_id, document_id, chunks)
    await _embed_pending_chunks(version_id, document_id)
    return await activate_version(version_id)
