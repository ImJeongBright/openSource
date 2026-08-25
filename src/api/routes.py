from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

import asyncpg
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status

from src.config import settings
from src.db import db
from src.models import (
    DocumentDetailResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentUploadResponse,
    ProcessingStatusResponse,
    VersionInfo,
)

app = FastAPI(title="OpenSQL Doc Search API", version="1.0.0")


SUPPORTED_EXTENSIONS: Dict[str, Tuple[str, str]] = {
    ".pdf": ("pdf", ".pdf"),
    ".txt": ("txt", ".txt"),
    ".md": ("markdown", ".md"),
    ".markdown": ("markdown", ".md"),
}


class DuplicateUploadError(RuntimeError):
    pass


class EmbeddingModelNotConfiguredError(RuntimeError):
    pass


class DocumentNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    file_type: str
    extension: str
    file_size_bytes: int
    file_hash: str


@dataclass(frozen=True)
class RegisteredUpload:
    document_id: UUID
    version_id: UUID
    version_number: int
    final_path: Path


def _safe_unlink(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Cleanup failure must not hide the original request/DB error.
        return


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _parse_tags(value: Optional[str]) -> List[str]:
    if value is None:
        return []
    return list(dict.fromkeys(tag.strip() for tag in value.split(",") if tag.strip()))


def _model_registry_identity() -> Tuple[str, str]:
    model = settings.EMBEDDING_MODEL.strip()
    if ":" not in model:
        return model, "default"
    model_name, model_version = model.rsplit(":", 1)
    return model_name, model_version


async def _stage_upload(file: UploadFile) -> StagedUpload:
    filename = file.filename or ""
    source_extension = Path(filename).suffix.lower()
    file_type_config = SUPPORTED_EXTENSIONS.get(source_extension)
    if file_type_config is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="지원 형식은 PDF, TXT, Markdown입니다.",
        )

    if settings.MAX_FILE_SIZE_MB <= 0:
        raise RuntimeError("MAX_FILE_SIZE_MB must be greater than zero")
    if settings.UPLOAD_STREAM_CHUNK_SIZE_BYTES <= 0:
        raise RuntimeError("UPLOAD_STREAM_CHUNK_SIZE_BYTES must be greater than zero")

    upload_directory = Path(settings.UPLOAD_DIR).resolve()
    await asyncio.to_thread(upload_directory.mkdir, parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="upload-",
        suffix=".tmp",
        dir=str(upload_directory),
        delete=False,
    )
    temporary_path = Path(temporary.name)
    digest = hashlib.sha256()
    size = 0
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024

    try:
        while True:
            chunk = await file.read(settings.UPLOAD_STREAM_CHUNK_SIZE_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"파일은 {settings.MAX_FILE_SIZE_MB}MB 이하여야 합니다.",
                )
            digest.update(chunk)
            await asyncio.to_thread(temporary.write, chunk)
        await asyncio.to_thread(temporary.flush)
    except (Exception, asyncio.CancelledError):
        temporary.close()
        _safe_unlink(temporary_path)
        raise
    finally:
        if not temporary.closed:
            temporary.close()

    if size == 0:
        _safe_unlink(temporary_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="빈 파일은 업로드할 수 없습니다.",
        )

    file_type, canonical_extension = file_type_config
    return StagedUpload(
        path=temporary_path,
        file_type=file_type,
        extension=canonical_extension,
        file_size_bytes=size,
        file_hash=digest.hexdigest(),
    )


async def _register_upload(
    staged: StagedUpload,
    title: str,
    category: Optional[str],
    tags: Sequence[str],
    uploader_id: Optional[str],
    existing_document_id: Optional[UUID],
) -> RegisteredUpload:
    final_path: Optional[Path] = None
    moved_to_final = False
    model_name, model_version = _model_registry_identity()

    try:
        async with db.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    staged.file_hash,
                )

                duplicate = await connection.fetchrow(
                    """
                    SELECT d.id AS document_id, dv.id AS version_id
                    FROM doc_search.document_versions dv
                    JOIN doc_search.documents d ON d.id = dv.document_id
                    WHERE dv.file_hash = $1
                      AND d.is_deleted = FALSE
                    LIMIT 1
                    """,
                    staged.file_hash,
                )
                if duplicate is not None:
                    raise DuplicateUploadError("동일한 내용의 파일이 이미 등록되어 있습니다.")

                embedding_model_id = await connection.fetchval(
                    """
                    SELECT id
                    FROM doc_search.embedding_models
                    WHERE model_name = $1
                      AND model_version = $2
                      AND provider = $3
                      AND dimensions = $4
                      AND is_active = TRUE
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    model_name,
                    model_version,
                    settings.EMBEDDING_API_PROVIDER.lower(),
                    settings.EMBEDDING_DIMENSIONS,
                )
                if embedding_model_id is None:
                    raise EmbeddingModelNotConfiguredError(
                        "활성 Qwen 임베딩 모델이 DB 레지스트리에 없습니다."
                    )

                if existing_document_id is None:
                    document_id = await connection.fetchval(
                        """
                        INSERT INTO doc_search.documents (
                            title, file_type, file_size_bytes, category, tags, uploader_id
                        ) VALUES ($1, $2, $3, $4, $5::text[], $6)
                        RETURNING id
                        """,
                        title,
                        staged.file_type,
                        staged.file_size_bytes,
                        category,
                        list(tags),
                        uploader_id,
                    )
                    version_number = 1
                else:
                    document = await connection.fetchrow(
                        """
                        SELECT id
                        FROM doc_search.documents
                        WHERE id = $1 AND is_deleted = FALSE
                        FOR UPDATE
                        """,
                        existing_document_id,
                    )
                    if document is None:
                        raise DocumentNotFoundError("버전을 추가할 문서를 찾을 수 없습니다.")
                    document_id = existing_document_id
                    version_number = await connection.fetchval(
                        """
                        SELECT COALESCE(MAX(version_number), 0) + 1
                        FROM doc_search.document_versions
                        WHERE document_id = $1
                        """,
                        document_id,
                    )
                    await connection.execute(
                        """
                        UPDATE doc_search.documents
                        SET title = $2,
                            file_type = $3,
                            file_size_bytes = $4,
                            category = $5,
                            tags = $6::text[],
                            uploader_id = COALESCE($7, uploader_id),
                            updated_at = NOW()
                        WHERE id = $1
                        """,
                        document_id,
                        title,
                        staged.file_type,
                        staged.file_size_bytes,
                        category,
                        list(tags),
                        uploader_id,
                    )

                version_id = await connection.fetchval(
                    """
                    INSERT INTO doc_search.document_versions (
                        document_id, version_number, status, embedding_model_id,
                        chunk_size, chunk_overlap, file_hash
                    ) VALUES ($1, $2, 'PENDING', $3, $4, $5, $6)
                    RETURNING id
                    """,
                    document_id,
                    version_number,
                    embedding_model_id,
                    settings.CHUNK_SIZE,
                    settings.CHUNK_OVERLAP,
                    staged.file_hash,
                )
                await connection.execute(
                    """
                    INSERT INTO doc_search.change_log (
                        event_type, status, document_id, version_id, max_retries
                    ) VALUES ('UPLOAD', 'PENDING', $1, $2, $3)
                    """,
                    document_id,
                    version_id,
                    settings.MAX_RETRIES,
                )

                final_path = Path(settings.UPLOAD_DIR).resolve() / (
                    f"{version_id}{staged.extension}"
                )
                if final_path.exists():
                    raise FileExistsError(f"upload target already exists: {final_path.name}")
                await asyncio.to_thread(os.replace, staged.path, final_path)
                moved_to_final = True

        if final_path is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("final upload path was not created")
        return RegisteredUpload(
            document_id=document_id,
            version_id=version_id,
            version_number=version_number,
            final_path=final_path,
        )
    except (Exception, asyncio.CancelledError):
        if moved_to_final:
            _safe_unlink(final_path)
        raise


async def _list_documents(
    category: Optional[str],
    created_after: Optional[datetime],
    created_before: Optional[datetime],
    limit: int,
    offset: int,
) -> Tuple[List[DocumentListItem], int]:
    clauses = ["d.is_deleted = FALSE"]
    arguments: List[Any] = []

    def bind(value: Any) -> str:
        arguments.append(value)
        return f"${len(arguments)}"

    if category is not None:
        clauses.append(f"d.category = {bind(category)}")
    if created_after is not None:
        clauses.append(f"d.created_at >= {bind(created_after)}")
    if created_before is not None:
        clauses.append(f"d.created_at <= {bind(created_before)}")

    limit_placeholder = bind(limit)
    offset_placeholder = bind(offset)
    query = f"""
        SELECT
            d.id AS document_id,
            d.title,
            d.file_type,
            d.file_size_bytes,
            d.category,
            COALESCE(d.tags, ARRAY[]::text[]) AS tags,
            d.created_at,
            latest.version_number AS latest_version_number,
            latest.status::text AS latest_version_status,
            COUNT(*) OVER()::integer AS total_count
        FROM doc_search.documents d
        LEFT JOIN LATERAL (
            SELECT dv.version_number, dv.status
            FROM doc_search.document_versions dv
            WHERE dv.document_id = d.id
            ORDER BY dv.version_number DESC
            LIMIT 1
        ) latest ON TRUE
        WHERE {' AND '.join(clauses)}
        ORDER BY d.created_at DESC, d.id
        LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """

    async with db.connection() as connection:
        rows = await connection.fetch(query, *arguments)
    total = int(rows[0]["total_count"]) if rows else 0
    items = [
        DocumentListItem.model_validate(
            {key: value for key, value in dict(row).items() if key != "total_count"}
        )
        for row in rows
    ]
    return items, total


async def _fetch_document_detail(document_id: UUID) -> Optional[DocumentDetailResponse]:
    async with db.connection() as connection:
        document = await connection.fetchrow(
            """
            SELECT id AS document_id, title, file_type, file_size_bytes,
                   category, COALESCE(tags, ARRAY[]::text[]) AS tags,
                   uploader_id, created_at, updated_at
            FROM doc_search.documents
            WHERE id = $1 AND is_deleted = FALSE
            """,
            document_id,
        )
        if document is None:
            return None
        version_rows = await connection.fetch(
            """
            SELECT id AS version_id, version_number, status::text AS status,
                   total_chunks, embedded_chunks, created_at
            FROM doc_search.document_versions
            WHERE document_id = $1
            ORDER BY version_number DESC
            """,
            document_id,
        )

    payload = dict(document)
    payload["versions"] = [VersionInfo.model_validate(dict(row)) for row in version_rows]
    return DocumentDetailResponse.model_validate(payload)


async def _fetch_status(document_id: UUID) -> Optional[ProcessingStatusResponse]:
    async with db.connection() as connection:
        row = await connection.fetchrow(
            """
            SELECT document_id, document_title, version_id, version_number,
                   version_status::text AS version_status,
                   total_chunks, embedded_chunks,
                   embedding_progress_pct::float AS embedding_progress_pct,
                   job_status::text AS job_status, retry_count, error_message
            FROM doc_search.processing_status
            WHERE document_id = $1
            ORDER BY version_number DESC, version_created_at DESC
            LIMIT 1
            """,
            document_id,
        )
    if row is None:
        return None
    return ProcessingStatusResponse.model_validate(dict(row))


@app.post(
    "/api/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    category: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    uploader_id: Optional[str] = Form(None),
    document_id: Optional[UUID] = Form(None),
) -> DocumentUploadResponse:
    normalized_title = title.strip()
    if not normalized_title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="문서 제목은 비어 있을 수 없습니다.",
        )

    staged: Optional[StagedUpload] = None
    try:
        staged = await _stage_upload(file)
        registered = await _register_upload(
            staged=staged,
            title=normalized_title,
            category=_normalize_optional_text(category),
            tags=_parse_tags(tags),
            uploader_id=_normalize_optional_text(uploader_id),
            existing_document_id=document_id,
        )
        return DocumentUploadResponse(
            document_id=registered.document_id,
            version_id=registered.version_id,
            version_number=registered.version_number,
            status="PENDING",
            file_hash=staged.file_hash,
        )
    except DuplicateUploadError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EmbeddingModelNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except asyncpg.PostgresError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenSQL에 문서를 등록하지 못했습니다.",
        ) from exc
    finally:
        await file.close()
        if staged is not None:
            _safe_unlink(staged.path)


@app.get("/api/documents", response_model=DocumentListResponse)
async def list_documents(
    category: Optional[str] = Query(None),
    created_after: Optional[datetime] = Query(None),
    created_before: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DocumentListResponse:
    if created_after is not None and created_before is not None:
        if created_after > created_before:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="created_after는 created_before보다 늦을 수 없습니다.",
            )
    items, total = await _list_documents(
        _normalize_optional_text(category),
        created_after,
        created_before,
        limit,
        offset,
    )
    return DocumentListResponse(items=items, total=total, limit=limit, offset=offset)


@app.get("/api/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document(document_id: UUID) -> DocumentDetailResponse:
    detail = await _fetch_document_detail(document_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="문서를 찾을 수 없습니다."
        )
    return detail


@app.get(
    "/api/documents/{document_id}/status",
    response_model=ProcessingStatusResponse,
)
async def get_document_status(document_id: UUID) -> ProcessingStatusResponse:
    processing_status = await _fetch_status(document_id)
    if processing_status is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="처리 상태를 찾을 수 없습니다.",
        )
    return processing_status
