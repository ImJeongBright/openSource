from __future__ import annotations

from uuid import uuid4

import pytest

from src import mcp_tools
from src.mcp_tools import TOOL_DEFINITIONS, MCPToolError, execute_tool
from src.models import SearchResult


def test_exactly_four_tools_are_exposed() -> None:
    assert {definition["name"] for definition in TOOL_DEFINITIONS} == {
        "search_documents",
        "get_document",
        "list_documents",
        "get_chunk",
    }


@pytest.mark.asyncio
async def test_search_tool_returns_all_traceability_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_id = uuid4()
    document_id = uuid4()

    async def fake_search(query, top_k, filters):
        assert query == "암호 정책"
        assert top_k == 2
        assert filters.category == "보안"
        return [
            SearchResult(
                chunk_id=chunk_id,
                chunk_text="원문",
                document_id=document_id,
                document_title="정책",
                version_number=2,
                page_number=7,
                section_title="암호",
                similarity=0.88,
            )
        ]

    monkeypatch.setattr(mcp_tools, "search", fake_search)
    result = await execute_tool(
        "search_documents",
        {"query": "암호 정책", "top_k": 2, "filters": {"category": "보안"}},
    )

    assert result[0]["chunk_id"] == str(chunk_id)
    assert result[0]["document_title"] == "정책"
    assert result[0]["version_number"] == 2
    assert result[0]["page_number"] == 7
    assert result[0]["section_title"] == "암호"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, arguments, message",
    [
        ("unknown", {}, "unknown tool"),
        ("search_documents", {"query": ""}, "non-empty"),
        ("get_document", {"document_id": "bad"}, "valid UUID"),
        ("get_chunk", {"chunk_id": "bad"}, "valid UUID"),
        ("list_documents", {"page": 0}, "positive"),
        ("list_documents", {"page_size": 101}, "between"),
    ],
)
async def test_tool_validation(name, arguments, message) -> None:
    with pytest.raises(MCPToolError, match=message):
        await execute_tool(name, arguments)


@pytest.mark.asyncio
async def test_rejects_unknown_arguments() -> None:
    with pytest.raises(MCPToolError, match="unknown arguments"):
        await execute_tool("search_documents", {"query": "q", "extra": True})


@pytest.mark.asyncio
async def test_repository_handlers_are_called_with_validated_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    chunk_id = uuid4()
    observed = {}

    async def fake_document(value, version):
        observed["document"] = (value, version)
        return {"document_id": str(value), "versions": []}

    async def fake_list(category, tags, page, page_size):
        observed["list"] = (category, tags, page, page_size)
        return {"items": [], "total": 0}

    async def fake_chunk(value):
        observed["chunk"] = value
        return {"chunk_id": str(value)}

    monkeypatch.setattr(mcp_tools, "_get_document", fake_document)
    monkeypatch.setattr(mcp_tools, "_list_documents", fake_list)
    monkeypatch.setattr(mcp_tools, "_get_chunk", fake_chunk)

    await execute_tool("get_document", {"document_id": str(document_id), "version": 2})
    await execute_tool(
        "list_documents",
        {"filters": {"category": "보안", "tags": ["정책"]}, "page": 2},
    )
    await execute_tool("get_chunk", {"chunk_id": str(chunk_id)})

    assert observed["document"] == (document_id, 2)
    assert observed["list"] == ("보안", ["정책"], 2, 50)
    assert observed["chunk"] == chunk_id
