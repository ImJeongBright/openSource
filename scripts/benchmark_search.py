#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_search import percentile  # noqa: E402
from src.db import db  # noqa: E402
from src.search.engine import search  # noqa: E402


def load_queries(path: Path) -> list[str]:
    queries = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
            query = str(payload["query"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid query on line {line_number}: {exc}") from exc
        if query:
            queries.append(query)
    if not queries:
        raise ValueError("benchmark dataset has no queries")
    return queries


async def benchmark(
    queries: list[str],
    requests: int,
    concurrency: int,
    top_k: int,
    warmup: int,
) -> dict[str, Any]:
    for index in range(warmup):
        await search(queries[index % len(queries)], top_k=top_k)

    semaphore = asyncio.Semaphore(concurrency)

    async def execute(index: int) -> tuple[float, str | None]:
        async with semaphore:
            started = time.perf_counter()
            try:
                await search(queries[index % len(queries)], top_k=top_k)
            except Exception as exc:  # The report needs the failure type, not a hidden failure.
                return (time.perf_counter() - started) * 1000.0, type(exc).__name__
            return (time.perf_counter() - started) * 1000.0, None

    started = time.perf_counter()
    results = await asyncio.gather(*(execute(index) for index in range(requests)))
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, error in results if error is None]
    errors: dict[str, int] = {}
    for _, error in results:
        if error is not None:
            errors[error] = errors.get(error, 0) + 1
    successes = len(latencies)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "top_k": top_k,
        "successes": successes,
        "success_rate": successes / requests,
        "throughput_rps": successes / elapsed if elapsed else 0.0,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies, default=0.0),
        },
        "errors": errors,
    }


async def async_main(arguments: argparse.Namespace) -> int:
    try:
        report = await benchmark(
            load_queries(arguments.dataset),
            arguments.requests,
            arguments.concurrency,
            arguments.top_k,
            arguments.warmup,
        )
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.output:
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        if arguments.enforce and report["success_rate"] < arguments.min_success_rate:
            return 2
        if arguments.enforce and report["latency_ms"]["p95"] > arguments.max_p95_ms:
            return 3
        return 0
    finally:
        await db.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent end-to-end semantic search benchmark")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--min-success-rate", type=float, default=0.995)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
