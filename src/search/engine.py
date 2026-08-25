from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from src.config import settings
from src.db import db
from src.embedding.client import generate_embeddings
from src.models import SearchFilters, SearchResult


class SearchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SearchStatement:
    sql: str
    arguments: Tuple[Any, ...]


def _vector_literal(vector: Sequence[float]) -> str:
    if len(vector) != settings.EMBEDDING_DIMENSIONS:
        raise SearchValidationError(
            f"query embedding has {len(vector)} dimensions; "
            f"expected {settings.EMBEDDING_DIMENSIONS}"
        )
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in vector
    ):
        raise SearchValidationError("query embedding contains an invalid value")
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


def _build_search_statement(
    query_vector: Sequence[float],
    top_k: int,
    filters: SearchFilters,
) -> SearchStatement:
    if top_k < 1 or top_k > settings.SEARCH_MAX_TOP_K:
        raise SearchValidationError(f"top_k must be between 1 and {settings.SEARCH_MAX_TOP_K}")
    if (
        filters.created_after is not None
        and filters.created_before is not None
        and filters.created_after > filters.created_before
    ):
        raise SearchValidationError("created_after must not be later than created_before")

    min_similarity = (
        settings.SEARCH_MIN_SIMILARITY if filters.min_similarity is None else filters.min_similarity
    )
    if not -1.0 <= min_similarity <= 1.0:
        raise SearchValidationError("min_similarity must be between -1 and 1")

    arguments: List[Any] = [_vector_literal(query_vector)]
    conditions = ["dv.status = 'ACTIVE'", "d.is_deleted = FALSE"]

    def bind(value: Any) -> str:
        arguments.append(value)
        return f"${len(arguments)}"

    if filters.category is not None:
        conditions.append(f"d.category = {bind(filters.category)}")
    if filters.tags:
        conditions.append(f"{bind(filters.tags)}::text[] <@ COALESCE(d.tags, ARRAY[]::text[])")
    if filters.document_id is not None:
        conditions.append(f"d.id = {bind(filters.document_id)}")
    if filters.created_after is not None:
        conditions.append(f"d.created_at >= {bind(filters.created_after)}")
    if filters.created_before is not None:
        conditions.append(f"d.created_at <= {bind(filters.created_before)}")
    conditions.append(f"1 - (e.vector <=> $1::vector) >= {bind(min_similarity)}")
    limit_placeholder = bind(top_k)

    sql = f"""
        SELECT
            c.id AS chunk_id,
            c.content AS chunk_text,
            d.id AS document_id,
            d.title AS document_title,
            dv.version_number,
            c.page_number,
            c.section_title,
            (1 - (e.vector <=> $1::vector))::float AS similarity
        FROM doc_search.embeddings e
        JOIN doc_search.chunks c ON c.id = e.chunk_id
        JOIN doc_search.document_versions dv ON dv.id = e.version_id
        JOIN doc_search.documents d ON d.id = e.document_id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.vector <=> $1::vector
        LIMIT {limit_placeholder}
    """
    return SearchStatement(sql=sql, arguments=tuple(arguments))


async def _execute_search(statement: SearchStatement) -> List[SearchResult]:
    async with db.connection() as connection:
        rows = await connection.fetch(statement.sql, *statement.arguments)
    return [SearchResult.model_validate(dict(row)) for row in rows]


async def search(
    query: str,
    top_k: Optional[int] = None,
    category: Optional[str] = None,
    filters: Optional[SearchFilters] = None,
) -> List[SearchResult]:
    """Embed a natural-language query and search only ACTIVE versions."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    normalized_query = query.strip()
    if not normalized_query:
        raise SearchValidationError("query must not be empty")

    effective_top_k = settings.SEARCH_DEFAULT_TOP_K if top_k is None else top_k
    effective_filters = filters.model_copy(deep=True) if filters is not None else SearchFilters()
    if category is not None:
        normalized_category = category.strip()
        if effective_filters.category not in (None, normalized_category):
            raise SearchValidationError("category arguments conflict")
        effective_filters.category = normalized_category or None

    instruction = settings.EMBEDDING_QUERY_INSTRUCTION.strip()
    embedding_input = (
        f"Instruct: {instruction}\nQuery: {normalized_query}" if instruction else normalized_query
    )
    embeddings = await generate_embeddings([embedding_input])
    if len(embeddings) != 1:
        raise SearchValidationError("embedding client returned an unexpected result count")
    statement = _build_search_statement(
        embeddings[0],
        effective_top_k,
        effective_filters,
    )
    return await _execute_search(statement)
