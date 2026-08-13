from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

# ------------------------------------------------------------------
# 추출(Extraction) 단계 반환 모델
# ------------------------------------------------------------------
class TextBlock(BaseModel):
    """문서에서 추출된 단일 텍스트 블록 (단락, 문장 등)"""
    text: str
    page: Optional[int] = None
    section: Optional[str] = None

# ------------------------------------------------------------------
# 청킹(Chunking) 단계 반환 모델
# ------------------------------------------------------------------
class ChunkData(BaseModel):
    """청킹이 완료된 데이터"""
    index: int
    content: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None

# ------------------------------------------------------------------
# API 및 공통 메타데이터 모델
# ------------------------------------------------------------------
class DocumentMeta(BaseModel):
    """문서의 메타데이터"""
    title: str
    file_type: str
    file_size_bytes: int
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

class VersionInfo(BaseModel):
    """문서 버전 정보"""
    version_id: UUID
    version_number: int
    status: str
    total_chunks: int
    embedded_chunks: int
    created_at: datetime

class SearchResult(BaseModel):
    """검색 결과 단일 항목"""
    chunk_id: UUID
    chunk_text: str
    document_id: UUID
    document_title: str
    version_number: int
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    similarity: float
