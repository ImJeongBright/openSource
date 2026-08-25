from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_mcp_entrypoint_loads_external_sdk_without_shadowing() -> None:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["OPENSQL_PASSWORD"] = "test-password"
    result = subprocess.run(
        [sys.executable, "mcp/server.py", "--check"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(",") == [
        "search_documents",
        "get_document",
        "list_documents",
        "get_chunk",
    ]


@pytest.mark.asyncio
async def test_mcp_stdio_handshake_and_tool_listing() -> None:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["OPENSQL_PASSWORD"] = "test-password"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(project_root / "mcp" / "server.py")],
        env=environment,
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            result = await session.list_tools()

    assert initialized.serverInfo.name == "opensql-doc-search"
    assert [tool.name for tool in result.tools] == [
        "search_documents",
        "get_document",
        "list_documents",
        "get_chunk",
    ]
