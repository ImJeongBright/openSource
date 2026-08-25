#!/usr/bin/env python3
from __future__ import annotations

# Import the external MCP package before adding the repository root to sys.path.
# The project directory is also named "mcp", so this ordering prevents shadowing.
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import db  # noqa: E402
from src.mcp_tools import TOOL_DEFINITIONS, MCPToolError, execute_tool  # noqa: E402

server = Server("opensql-doc-search")


@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    return [types.Tool(**definition) for definition in TOOL_DEFINITIONS]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: Optional[Dict[str, Any]],
) -> List[types.TextContent]:
    try:
        result = await execute_tool(name, arguments)
    except MCPToolError:
        raise
    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        )
    ]


async def main() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            )
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if "--check" in sys.argv:
        print(",".join(definition["name"] for definition in TOOL_DEFINITIONS))
    else:
        asyncio.run(main())
