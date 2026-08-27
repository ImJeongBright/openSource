#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings  # noqa: E402
from src.db import db  # noqa: E402
from src.embedding.client import generate_embeddings  # noqa: E402
from src.models import SearchFilters  # noqa: E402
from src.search.engine import _build_search_statement  # noqa: E402


def walk_plan(plan: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from walk_plan(child)


async def explain(query: str, top_k: int) -> dict[str, Any]:
    instruction = settings.EMBEDDING_QUERY_INSTRUCTION.strip()
    embedding_input = f"Instruct: {instruction}\nQuery: {query}" if instruction else query
    vector = (await generate_embeddings([embedding_input]))[0]
    statement = _build_search_statement(vector, top_k, SearchFilters())
    async with db.connection() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('hnsw.ef_search', $1, TRUE)",
                str(settings.SEARCH_HNSW_EF_SEARCH),
            )
            row = await connection.fetchval(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement.sql,
                *statement.arguments,
            )
    if isinstance(row, str):
        row = json.loads(row)
    report = row[0]
    nodes = list(walk_plan(report["Plan"]))
    indexes = sorted({node["Index Name"] for node in nodes if node.get("Index Name")})
    return {
        "planning_time_ms": report.get("Planning Time"),
        "execution_time_ms": report.get("Execution Time"),
        "node_types": sorted({str(node.get("Node Type")) for node in nodes}),
        "indexes": indexes,
        "hnsw_index_used": "idx_embeddings_hnsw" in indexes,
        "note": "Small tables may correctly use a sequential scan; require HNSW only at scale.",
    }


async def async_main(arguments: argparse.Namespace) -> int:
    try:
        report = await explain(arguments.query, arguments.top_k)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if arguments.require_hnsw and not report["hnsw_index_used"] else 0
    finally:
        await db.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the real pgvector search execution plan")
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--require-hnsw", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
