from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.db import db


class VersionerError(RuntimeError):
    """Base exception for version validation and activation failures."""


class VersionNotFoundError(VersionerError):
    """Raised when the requested document version does not exist."""


class VersionNotReadyError(VersionerError):
    """Raised when a version has missing chunks or embeddings."""


class VersionStateError(VersionerError):
    """Raised when a version cannot be activated from its current state."""


VERSION_LOCK_SQL = """
SELECT document_id, status::text AS status
FROM doc_search.document_versions
WHERE id = $1
FOR UPDATE
"""

VERSION_COMPLETENESS_SQL = """
SELECT
    COUNT(c.id)::integer AS total_chunks,
    COUNT(e.id)::integer AS embedded_chunks,
    COUNT(c.id) FILTER (WHERE e.id IS NULL)::integer AS missing_embeddings,
    COUNT(c.id) FILTER (WHERE NOT c.is_embedded)::integer AS unmarked_chunks
FROM doc_search.document_versions dv
LEFT JOIN doc_search.chunks c
    ON c.version_id = dv.id
   AND c.document_id = dv.document_id
LEFT JOIN doc_search.embeddings e
    ON e.chunk_id = c.id
   AND e.version_id = dv.id
   AND e.document_id = dv.document_id
WHERE dv.id = $1
GROUP BY dv.id
"""

ACTIVATE_VERSION_SQL = "SELECT doc_search.activate_version($1)"


@dataclass(frozen=True)
class VersionActivationResult:
    version_id: UUID
    document_id: UUID
    total_chunks: int
    embedded_chunks: int
    already_active: bool = False


def _count(row: Any, key: str) -> int:
    return int(row[key] or 0)


async def activate_version(version_id: UUID) -> VersionActivationResult:
    """Validate a version and atomically make it the document's ACTIVE version.

    Validation and the stored-function call share one transaction. Locking the
    version row prevents an embedding batch from changing the data between the
    completeness check and activation. The stored function locks the document
    row and archives the previous ACTIVE version atomically.
    """
    async with db.connection() as connection:
        async with connection.transaction():
            version_row = await connection.fetchrow(VERSION_LOCK_SQL, version_id)
            if version_row is None:
                raise VersionNotFoundError(f"document version not found: {version_id}")

            row = await connection.fetchrow(VERSION_COMPLETENESS_SQL, version_id)
            if row is None:  # pragma: no cover - protected by VERSION_LOCK_SQL
                raise VersionNotFoundError(f"document version not found: {version_id}")

            status = str(version_row["status"])
            document_id = version_row["document_id"]
            total_chunks = _count(row, "total_chunks")
            embedded_chunks = _count(row, "embedded_chunks")
            missing_embeddings = _count(row, "missing_embeddings")
            unmarked_chunks = _count(row, "unmarked_chunks")

            if status == "ACTIVE":
                return VersionActivationResult(
                    version_id=version_id,
                    document_id=document_id,
                    total_chunks=total_chunks,
                    embedded_chunks=embedded_chunks,
                    already_active=True,
                )
            if status != "PROCESSING":
                raise VersionStateError(
                    f"version {version_id} must be PROCESSING, got {status}"
                )
            if (
                total_chunks == 0
                or total_chunks != embedded_chunks
                or missing_embeddings != 0
                or unmarked_chunks != 0
            ):
                raise VersionNotReadyError(
                    "version is not complete: "
                    f"total={total_chunks}, embedded={embedded_chunks}, "
                    f"missing_embeddings={missing_embeddings}, "
                    f"unmarked_chunks={unmarked_chunks}"
                )

            await connection.execute(ACTIVATE_VERSION_SQL, version_id)

    return VersionActivationResult(
        version_id=version_id,
        document_id=document_id,
        total_chunks=total_chunks,
        embedded_chunks=embedded_chunks,
    )
