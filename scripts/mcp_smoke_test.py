#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def decode(result: Any) -> Any:
    if getattr(result, "isError", False):
        raise RuntimeError("MCP tool returned an error")
    if not result.content or not hasattr(result.content[0], "text"):
        raise RuntimeError("MCP tool returned no JSON text")
    return json.loads(result.content[0].text)


async def smoke_test(document_id: str, query: str) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(PROJECT_ROOT / ".venv/bin/python"),
        args=[str(PROJECT_ROOT / "mcp/server.py")],
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            expected = {"search_documents", "get_document", "list_documents", "get_chunk"}
            if set(names) != expected:
                raise AssertionError(f"unexpected MCP tools: {names}")
            listed = decode(await session.call_tool("list_documents", {}))
            document = decode(
                await session.call_tool("get_document", {"document_id": document_id})
            )
            results = decode(
                await session.call_tool(
                    "search_documents",
                    {"query": query, "top_k": 5, "filters": {"document_id": document_id}},
                )
            )
            if not results:
                raise AssertionError("MCP search returned no results")
            chunk = decode(
                await session.call_tool("get_chunk", {"chunk_id": results[0]["chunk_id"]})
            )
            if document["document_id"] != document_id or chunk["document_id"] != document_id:
                raise AssertionError("MCP traceability mismatch")
            return {
                "initialized": True,
                "tools": names,
                "visible_documents": listed["total"],
                "results": len(results),
                "top_similarity": results[0]["similarity"],
                "traceability": True,
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise all four tools over MCP stdio")
    parser.add_argument("document_id")
    parser.add_argument("--query", default="문서의 핵심 운영 절차는 무엇입니까?")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    print(
        json.dumps(
            asyncio.run(smoke_test(arguments.document_id, arguments.query)),
            ensure_ascii=False,
            indent=2,
        )
    )
