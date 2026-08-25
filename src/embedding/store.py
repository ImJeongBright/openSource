from __future__ import annotations

import math
from typing import List, Sequence
from uuid import UUID

import asyncpg

from src.config import settings
from src.db import db
from src.models import EmbeddingBatchResult, EmbeddingRecord


class EmbeddingBatchError(RuntimeError):
    """Raised when an embedding batch violates the DB handoff contract."""


def _validate_records(records: Sequence[EmbeddingRecord]) -> List[EmbeddingRecord]:
    if not isinstance(records, (list, tuple)):
        raise TypeError("records must be a list or tuple of EmbeddingRecord values")
    if len(records) > settings.EMBEDDING_BATCH_SIZE:
        raise EmbeddingBatchError(
            "embedding batch exceeds EMBEDDING_BATCH_SIZE: "
            f"{len(records)} > {settings.EMBEDDING_BATCH_SIZE}"
        )

    validated: List[EmbeddingRecord] = []
    seen_chunk_ids = set()
    for index, record in enumerate(records):
        if not isinstance(record, EmbeddingRecord):
            raise TypeError(f"records[{index}] must be an EmbeddingRecord")
        if record.chunk_id in seen_chunk_ids:
            raise EmbeddingBatchError(f"duplicate chunk_id in batch: {record.chunk_id}")
        seen_chunk_ids.add(record.chunk_id)

        if len(record.vector) != settings.EMBEDDING_DIMENSIONS:
            raise EmbeddingBatchError(
                f"records[{index}] has {len(record.vector)} dimensions; "
                f"expected {settings.EMBEDDING_DIMENSIONS}"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in record.vector
        ):
            raise EmbeddingBatchError(
                f"records[{index}] contains a non-finite or non-numeric value"
            )
        validated.append(record)
    return validated


def _to_vector_literal(vector: Sequence[float]) -> str:
    # Values are validated as finite numbers first; this text is still passed as
    # a bound parameter and never interpolated into SQL.
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


async def _save_with_connection(
    connection: asyncpg.Connection,
    version_id: UUID,
    document_id: UUID,
    records: Sequence[EmbeddingRecord],
) -> EmbeddingBatchResult:
    async with connection.transaction():
        version = await connection.fetchrow(
            """
            SELECT id, embedding_model_id
            FROM doc_search.document_versions
            WHERE id = $1 AND document_id = $2
            FOR UPDATE
            """,
            version_id,
            document_id,
        )
        if version is None:
            raise EmbeddingBatchError("document version does not exist")
        embedding_model_id = version["embedding_model_id"]
        if embedding_model_id is None:
            raise EmbeddingBatchError("document version has no embedding model")

        chunk_ids = [record.chunk_id for record in records]
        chunk_rows = await connection.fetch(
            """
            SELECT id
            FROM doc_search.chunks
            WHERE id = ANY($1::uuid[])
              AND version_id = $2
              AND document_id = $3
            FOR UPDATE
            """,
            chunk_ids,
            version_id,
            document_id,
        )
        found_chunk_ids = {row["id"] for row in chunk_rows}
        missing_chunk_ids = set(chunk_ids) - found_chunk_ids
        if missing_chunk_ids:
            missing = ", ".join(sorted(str(value) for value in missing_chunk_ids))
            raise EmbeddingBatchError(
                f"chunks do not belong to the requested document version: {missing}"
            )

        vector_literals = [_to_vector_literal(record.vector) for record in records]
        inserted_rows = await connection.fetch(
            """
            WITH input_rows AS (
                SELECT *
                FROM UNNEST($1::uuid[], $2::text[]) AS input(chunk_id, vector_text)
            )
            INSERT INTO doc_search.embeddings (
                chunk_id, version_id, document_id, embedding_model_id, vector
            )
            SELECT input.chunk_id, $3, $4, $5, input.vector_text::vector
            FROM input_rows input
            ON CONFLICT (chunk_id) DO NOTHING
            RETURNING chunk_id
            """,
            chunk_ids,
            vector_literals,
            version_id,
            document_id,
            embedding_model_id,
        )

        # This also repairs is_embedded after a prior process crashed between
        # an old non-atomic insert and its status update.
        await connection.execute(
            """
            UPDATE doc_search.chunks c
            SET is_embedded = TRUE,
                embedded_at = COALESCE(c.embedded_at, NOW())
            WHERE c.id = ANY($1::uuid[])
              AND EXISTS (
                  SELECT 1
                  FROM doc_search.embeddings e
                  WHERE e.chunk_id = c.id
              )
            """,
            chunk_ids,
        )

        embedded_count = await connection.fetchval(
            """
            SELECT COUNT(*)::integer
            FROM doc_search.embeddings
            WHERE version_id = $1
            """,
            version_id,
        )
        update_status = await connection.execute(
            """
            UPDATE doc_search.document_versions
            SET embedded_chunks = $2,
                updated_at = NOW()
            WHERE id = $1 AND document_id = $3
            """,
            version_id,
            embedded_count,
            document_id,
        )
        if update_status != "UPDATE 1":
            raise EmbeddingBatchError("failed to update document version progress")

    return EmbeddingBatchResult(
        requested_count=len(records),
        inserted_count=len(inserted_rows),
        embedded_count=int(embedded_count),
    )


async def save_embedding_batch(
    version_id: UUID,
    document_id: UUID,
    records: Sequence[EmbeddingRecord],
) -> EmbeddingBatchResult:
    """Persist one idempotent embedding batch in a single transaction."""
    validated = _validate_records(records)
    if not validated:
        return EmbeddingBatchResult(
            requested_count=0,
            inserted_count=0,
            embedded_count=0,
        )

    async with db.connection() as connection:
        return await _save_with_connection(
            connection,
            version_id,
            document_id,
            validated,
        )
