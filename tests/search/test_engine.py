from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.config import settings
from src.models import SearchFilters, SearchResult
from src.search import engine
from src.search.engine import SearchValidationError, search


def _vector(value: float = 0.1):
    return [value] * settings.EMBEDDING_DIMENSIONS


def test_statement_enforces_active_traceable_parameterized_search() -> None:
    document_id = uuid4()
    filters = SearchFilters(
        category="보안",
        tags=["정책", "2026"],
        document_id=document_id,
        created_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
        min_similarity=0.3,
    )
    statement = engine._build_search_statement(_vector(), 7, filters)

    assert "dv.status = 'ACTIVE'" in statement.sql
    assert "d.is_deleted = FALSE" in statement.sql
    assert "ORDER BY e.vector <=> $1::vector" in statement.sql
    assert "c.page_number" in statement.sql
    assert "c.section_title" in statement.sql
    assert "보안" not in statement.sql
    assert "정책" not in statement.sql
    assert statement.arguments[1] == "보안"
    assert statement.arguments[-1] == 7


@pytest.mark.parametrize("top_k", [0, -1, 101])
def test_statement_rejects_invalid_top_k(top_k: int) -> None:
    with pytest.raises(SearchValidationError, match="top_k"):
        engine._build_search_statement(_vector(), top_k, SearchFilters())


def test_statement_rejects_wrong_dimensions() -> None:
    with pytest.raises(SearchValidationError, match="dimensions"):
        engine._build_search_statement([0.1], 5, SearchFilters())


@pytest.mark.asyncio
async def test_search_embeds_once_and_returns_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_id = uuid4()
    document_id = uuid4()
    observed = {}

    async def fake_embeddings(texts):
        observed["texts"] = texts
        return [_vector()]

    async def fake_execute(statement):
        observed["statement"] = statement
        return [
            SearchResult(
                chunk_id=chunk_id,
                chunk_text="정책 원문",
                document_id=document_id,
                document_title="보안 정책",
                version_number=2,
                page_number=3,
                section_title="암호",
                similarity=0.91,
            )
        ]

    monkeypatch.setattr(engine, "generate_embeddings", fake_embeddings)
    monkeypatch.setattr(engine, "_execute_search", fake_execute)

    results = await search("  암호 정책  ", top_k=3, category="보안")

    assert observed["texts"] == [
        "Instruct: Given a Korean enterprise document search query, "
        "retrieve relevant passages that answer the query\nQuery: 암호 정책"
    ]
    assert observed["statement"].arguments[-1] == 3
    assert results[0].page_number == 3
    assert results[0].section_title == "암호"


@pytest.mark.asyncio
async def test_search_rejects_empty_query() -> None:
    with pytest.raises(SearchValidationError, match="empty"):
        await search("   ")


@pytest.mark.asyncio
async def test_search_rejects_conflicting_categories() -> None:
    with pytest.raises(SearchValidationError, match="conflict"):
        await search(
            "query",
            category="A",
            filters=SearchFilters(category="B"),
        )
