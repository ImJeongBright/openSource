#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import db  # noqa: E402
from src.mcp_tools import execute_tool  # noqa: E402


def uniquify_demo_content(path: Path, marker: str) -> bytes:
    """Change the file checksum without adding searchable marker text."""
    marker_digest = hashlib.sha256(marker.encode("utf-8")).digest()
    padding_size = 1 + int.from_bytes(marker_digest[:4]) % 2048
    return path.read_bytes() + b"\n" + (b" " * padding_size) + b"\n"


async def upload(
    client: httpx.AsyncClient,
    path: Path,
    title: str,
    marker: str,
    document_id: str | None = None,
) -> dict[str, Any]:
    content = uniquify_demo_content(path, marker)
    data = {
        "title": title,
        "category": "security-demo",
        "tags": "security,policy,demo",
        "uploader_id": "phase9-demo",
    }
    if document_id:
        data["document_id"] = document_id
    response = await client.post(
        "/api/documents",
        files={"file": (path.name, content, "text/markdown")},
        data=data,
    )
    if response.status_code != 202:
        raise RuntimeError(f"upload failed: HTTP {response.status_code} {response.text}")
    return response.json()


async def wait_active(
    client: httpx.AsyncClient,
    document_id: str,
    expected_version: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"/api/documents/{document_id}/status")
        if response.status_code != 200:
            raise RuntimeError(f"status failed: HTTP {response.status_code} {response.text}")
        payload = response.json()
        if payload["version_number"] == expected_version and payload["version_status"] == "ACTIVE":
            if payload["total_chunks"] != payload["embedded_chunks"]:
                raise AssertionError("ACTIVE version has incomplete embeddings")
            return payload
        if payload["version_status"] == "FAILED" or payload.get("job_status") == "DEAD_LETTER":
            raise RuntimeError(f"pipeline failed: {payload}")
        await asyncio.sleep(0.5)
    raise TimeoutError(f"version {expected_version} did not become ACTIVE")


async def active_version(client: httpx.AsyncClient, document_id: str) -> int | None:
    response = await client.get("/api/documents", params={"category": "security-demo"})
    response.raise_for_status()
    for item in response.json()["items"]:
        if item["document_id"] == document_id:
            return item["latest_version_number"]
    return None


async def run_demo(arguments: argparse.Namespace) -> dict[str, Any]:
    marker = uuid4().hex
    created_documents: list[str] = []
    report: dict[str, Any] = {"run_id": marker}
    async with httpx.AsyncClient(base_url=arguments.api_base, timeout=30.0) as client:
        try:
            v1 = await upload(client, arguments.v1, "보안 정책 V1", marker + "-v1")
            created_documents.append(v1["document_id"])
            await wait_active(client, v1["document_id"], 1, arguments.timeout)

            v2 = await upload(client, arguments.v2, "보안 정책 V2", marker + "-v2")
            created_documents.append(v2["document_id"])
            await wait_active(client, v2["document_id"], 1, arguments.timeout)

            update = await upload(
                client,
                arguments.v2,
                "보안 정책 V2",
                marker + "-v2-update",
                document_id=v2["document_id"],
            )
            active_while_pending = await active_version(client, v2["document_id"])
            if active_while_pending != 1:
                raise AssertionError("existing ACTIVE version disappeared during update")
            final_status = await wait_active(client, v2["document_id"], 2, arguments.timeout)
            if await active_version(client, v2["document_id"]) != 2:
                raise AssertionError("new version was not atomically activated")

            search_results = await execute_tool(
                "search_documents",
                {
                    "query": "올해 강화된 비밀번호 길이와 다중 인증 정책은 무엇입니까?",
                    "top_k": 5,
                    "filters": {"category": "security-demo"},
                },
            )
            if not search_results:
                raise AssertionError("MCP search returned no demo result")
            top = search_results[0]
            chunk = await execute_tool("get_chunk", {"chunk_id": top["chunk_id"]})
            if chunk["chunk_id"] != top["chunk_id"]:
                raise AssertionError("MCP search/get_chunk traceability mismatch")

            report.update(
                {
                    "documents": created_documents,
                    "version_update": {
                        "pending_version": update["version_number"],
                        "active_during_update": active_while_pending,
                        "final_active_version": final_status["version_number"],
                    },
                    "mcp": {
                        "search_results": len(search_results),
                        "top_document": top["document_title"],
                        "top_similarity": top["similarity"],
                        "traceability": True,
                    },
                }
            )
            return report
        finally:
            if arguments.cleanup:
                cleanup_errors = []
                for document_id in reversed(created_documents):
                    response = await client.delete(f"/api/documents/{document_id}")
                    if response.status_code != 200:
                        cleanup_errors.append(
                            {"document_id": document_id, "status": response.status_code}
                        )
                report["cleanup"] = {"attempted": True, "errors": cleanup_errors}


async def async_main(arguments: argparse.Namespace) -> int:
    try:
        report = await run_demo(arguments)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.output:
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        return 0
    finally:
        await db.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Phase 9 upload/version/MCP demo")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--v1",
        type=Path,
        default=PROJECT_ROOT / "samples/demo/security_policy_v1.md",
    )
    parser.add_argument(
        "--v2",
        type=Path,
        default=PROJECT_ROOT / "samples/demo/security_policy_v2.md",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
