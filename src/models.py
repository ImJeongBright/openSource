from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TextBlock(BaseModel):
    text: str
    page: Optional[int] = None
    section: Optional[str] = None


class ChunkData(BaseModel):
    index: int
    content: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None


class DocumentMeta(BaseModel):
    title: str
    file_type: str
    file_size_bytes: int
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class VersionInfo(BaseModel):
    version_id: UUID
    version_number: int
    status: str
    total_chunks: int
    embedded_chunks: int
    created_at: datetime
    processing_completed_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None


class SearchResult(BaseModel):
    chunk_id: UUID
    chunk_text: str
    document_id: UUID
    document_title: str
    version_number: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    similarity: float


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    document_id: Optional[UUID] = None
    title: Optional[str] = None
    created_after: Optional[datetime] = None
    created_before: Optional[datetime] = None
    min_similarity: Optional[float] = None


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    version_number: int
    status: str
    file_hash: str


class DocumentListItem(BaseModel):
    document_id: UUID
    title: str
    file_type: str
    file_size_bytes: Optional[int] = None
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None
    latest_version_number: Optional[int] = None
    latest_version_status: Optional[str] = None
    total_chunks: int = 0


class DocumentListResponse(BaseModel):
    items: List[DocumentListItem]
    total: int
    limit: int
    offset: int


class DocumentDetailResponse(BaseModel):
    document_id: UUID
    title: str
    file_type: str
    file_size_bytes: Optional[int] = None
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    uploader_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    versions: List[VersionInfo]


class ProcessingStatusResponse(BaseModel):
    document_id: UUID
    document_title: str
    version_id: UUID
    version_number: int
    version_status: str
    total_chunks: int
    embedded_chunks: int
    embedding_progress_pct: float
    job_status: Optional[str] = None
    retry_count: Optional[int] = None
    error_message: Optional[str] = None


class DocumentDeleteResponse(BaseModel):
    document_id: UUID
    status: str
    deleted_versions: int
    deleted_files: int


class HealthResponse(BaseModel):
    status: str
    service: str
    database: Optional[str] = None
    embedding: Optional[str] = None


class EmbeddingRecord(BaseModel):
    chunk_id: UUID
    vector: List[float]


class EmbeddingBatchResult(BaseModel):
    requested_count: int
    inserted_count: int
    embedded_count: int
