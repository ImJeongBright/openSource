from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import UUID

from pydantic import ValidationError

from src.config import settings
from src.db import db
from src.models import SearchFilters
from src.search.engine import SearchValidationError, search


class MCPToolError(ValueError):
    pass


TOOL_DEFINITIONS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "search_documents",
        "description": "ACTIVE 문서에서 자연어 의미 검색을 수행합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 100},
                "filters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "document_id": {"type": "string", "format": "uuid"},
                        "created_after": {"type": "string", "format": "date-time"},
                        "created_before": {"type": "string", "format": "date-time"},
                        "min_similarity": {
                            "type": "number",
                            "minimum": -1,
                            "maximum": 1,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_document",
        "description": "문서 메타데이터와 버전 정보를 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "format": "uuid"},
                "version": {"type": "integer", "minimum": 1},
            },
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_documents",
        "description": "현재 검색 가능한 ACTIVE 문서 목록을 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": False,
                },
                "page": {"type": "integer", "minimum": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_chunk",
        "description": "청크 원문과 문서·버전·페이지·섹션 출처를 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {"chunk_id": {"type": "string", "format": "uuid"}},
            "required": ["chunk_id"],
            "additionalProperties": False,
        },
    },
)


def _ensure_mapping(arguments: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if arguments is None:
        return {}
    if not isinstance(arguments, Mapping):
        raise MCPToolError("tool arguments must be an object")
    return dict(arguments)


def _reject_unknown(arguments: Mapping[str, Any], allowed: set) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise MCPToolError(f"unknown arguments: {', '.join(sorted(unknown))}")


def _parse_uuid(value: Any, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise MCPToolError(f"{field} must be a valid UUID") from exc


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


async def _get_document(document_id: UUID, version: Optional[int]) -> Dict[str, Any]:
    arguments: List[Any] = [document_id]
    version_clause = ""
    if version is not None:
        arguments.append(version)
        version_clause = "AND dv.version_number = $2"

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
            raise MCPToolError("document not found")
        versions = await connection.fetch(
            f"""
            SELECT dv.id AS version_id, dv.version_number, dv.status::text AS status,
                   dv.total_chunks, dv.embedded_chunks, dv.created_at,
                   dv.activated_at
            FROM doc_search.document_versions dv
            WHERE dv.document_id = $1 {version_clause}
            ORDER BY dv.version_number DESC
            """,
            *arguments,
        )

    result = dict(document)
    result["versions"] = [dict(row) for row in versions]
    return _json_safe(result)


async def _list_documents(
    category: Optional[str],
    tags: List[str],
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    conditions = ["dv.status = 'ACTIVE'", "d.is_deleted = FALSE"]
    arguments: List[Any] = []

    def bind(value: Any) -> str:
        arguments.append(value)
        return f"${len(arguments)}"

    if category is not None:
        conditions.append(f"d.category = {bind(category)}")
    if tags:
        conditions.append(f"{bind(tags)}::text[] <@ COALESCE(d.tags, ARRAY[]::text[])")
    limit_placeholder = bind(page_size)
    offset_placeholder = bind((page - 1) * page_size)

    query = f"""
        SELECT d.id AS document_id, d.title, d.file_type, d.category,
               COALESCE(d.tags, ARRAY[]::text[]) AS tags,
               dv.id AS version_id, dv.version_number,
               dv.total_chunks, dv.activated_at,
               COUNT(*) OVER()::integer AS total_count
        FROM doc_search.documents d
        JOIN doc_search.document_versions dv ON dv.document_id = d.id
        WHERE {' AND '.join(conditions)}
        ORDER BY d.title, d.id
        LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """
    async with db.connection() as connection:
        rows = await connection.fetch(query, *arguments)
    total = int(rows[0]["total_count"]) if rows else 0
    items = [
        {key: value for key, value in dict(row).items() if key != "total_count"} for row in rows
    ]
    return _json_safe({"items": items, "total": total, "page": page, "page_size": page_size})


async def _get_chunk(chunk_id: UUID) -> Dict[str, Any]:
    async with db.connection() as connection:
        row = await connection.fetchrow(
            """
            SELECT c.id AS chunk_id, c.content AS chunk_text, c.chunk_index,
                   d.id AS document_id, d.title AS document_title,
                   dv.id AS version_id, dv.version_number,
                   c.page_number, c.section_title, c.char_start, c.char_end
            FROM doc_search.chunks c
            JOIN doc_search.document_versions dv ON dv.id = c.version_id
            JOIN doc_search.documents d ON d.id = c.document_id
            WHERE c.id = $1
              AND dv.status = 'ACTIVE'
              AND d.is_deleted = FALSE
            """,
            chunk_id,
        )
    if row is None:
        raise MCPToolError("chunk not found in an ACTIVE document version")
    return _json_safe(dict(row))


async def execute_tool(
    name: str,
    arguments: Optional[Mapping[str, Any]],
) -> Any:
    values = _ensure_mapping(arguments)

    if name == "search_documents":
        _reject_unknown(values, {"query", "top_k", "filters"})
        query = values.get("query")
        if not isinstance(query, str) or not query.strip():
            raise MCPToolError("query must be a non-empty string")
        top_k = values.get("top_k", settings.SEARCH_DEFAULT_TOP_K)
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise MCPToolError("top_k must be an integer")
        raw_filters = values.get("filters", {})
        if not isinstance(raw_filters, Mapping):
            raise MCPToolError("filters must be an object")
        try:
            filters = SearchFilters.model_validate(dict(raw_filters))
            results = await search(query, top_k=top_k, filters=filters)
        except (ValidationError, SearchValidationError) as exc:
            raise MCPToolError(str(exc)) from exc
        return _json_safe([result.model_dump() for result in results])

    if name == "get_document":
        _reject_unknown(values, {"document_id", "version"})
        document_id = _parse_uuid(values.get("document_id"), "document_id")
        version = values.get("version")
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1
        ):
            raise MCPToolError("version must be a positive integer")
        return await _get_document(document_id, version)

    if name == "list_documents":
        _reject_unknown(values, {"filters", "page", "page_size"})
        raw_filters = values.get("filters", {})
        if not isinstance(raw_filters, Mapping):
            raise MCPToolError("filters must be an object")
        _reject_unknown(raw_filters, {"category", "tags"})
        category = raw_filters.get("category")
        if category is not None and not isinstance(category, str):
            raise MCPToolError("filters.category must be a string")
        tags = raw_filters.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise MCPToolError("filters.tags must be a list of strings")
        page = values.get("page", 1)
        page_size = values.get("page_size", 50)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise MCPToolError("page must be a positive integer")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise MCPToolError("page_size must be between 1 and 100")
        return await _list_documents(category, tags, page, page_size)

    if name == "get_chunk":
        _reject_unknown(values, {"chunk_id"})
        return await _get_chunk(_parse_uuid(values.get("chunk_id"), "chunk_id"))

    raise MCPToolError(f"unknown tool: {name}")
