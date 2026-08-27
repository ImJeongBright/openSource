#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import db  # noqa: E402
from src.models import SearchFilters, SearchResult  # noqa: E402
from src.search.engine import search  # noqa: E402


@dataclass(frozen=True)
class EvaluationCase:
    query: str
    expected: Mapping[str, Any]
    filters: SearchFilters


def percentile(values: Sequence[float], percentage: float) -> float:
    if not values:
        return 0.0
    if not 0.0 <= percentage <= 100.0:
        raise ValueError("percentage must be between 0 and 100")
    ordered = sorted(values)
    rank = max(0, math.ceil((percentage / 100.0) * len(ordered)) - 1)
    return float(ordered[rank])


def result_matches(result: SearchResult, expected: Mapping[str, Any]) -> bool:
    checks = []
    if expected.get("chunk_id"):
        checks.append(str(result.chunk_id) == str(expected["chunk_id"]))
    if expected.get("document_id"):
        checks.append(str(result.document_id) == str(expected["document_id"]))
    if expected.get("document_title"):
        checks.append(result.document_title == str(expected["document_title"]))
    if expected.get("text_contains"):
        checks.append(str(expected["text_contains"]) in result.chunk_text)
    if not checks:
        raise ValueError("expected must define a chunk, document, title, or text match")
    return all(checks)


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
            query = str(payload["query"]).strip()
            expected = payload["expected"]
            filters = SearchFilters.model_validate(payload.get("filters", {}))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid evaluation case on line {line_number}: {exc}") from exc
        if not query or not isinstance(expected, Mapping):
            raise ValueError(f"invalid evaluation case on line {line_number}")
        cases.append(EvaluationCase(query=query, expected=expected, filters=filters))
    if not cases:
        raise ValueError("evaluation dataset has no cases")
    return cases


async def evaluate(cases: Iterable[EvaluationCase], top_k: int) -> dict[str, Any]:
    reciprocal_ranks = []
    latencies_ms = []
    details = []
    for case in cases:
        started = time.perf_counter()
        results = await search(case.query, top_k=top_k, filters=case.filters)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        rank = next(
            (
                index
                for index, result in enumerate(results, 1)
                if result_matches(result, case.expected)
            ),
            None,
        )
        reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        details.append({"query": case.query, "hit": rank is not None, "rank": rank})

    total = len(details)
    hits = sum(1 for detail in details if detail["hit"])
    return {
        "cases": total,
        "top_k": top_k,
        "recall_at_k": hits / total,
        "mrr": sum(reciprocal_ranks) / total,
        "latency_ms": {
            "p50": percentile(latencies_ms, 50),
            "p95": percentile(latencies_ms, 95),
            "max": max(latencies_ms),
        },
        "details": details,
    }


async def async_main(arguments: argparse.Namespace) -> int:
    try:
        report = await evaluate(load_cases(arguments.dataset), arguments.top_k)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        print(rendered)
        if arguments.output:
            arguments.output.write_text(rendered + "\n", encoding="utf-8")
        if report["recall_at_k"] < arguments.min_recall:
            return 2
        if report["mrr"] < arguments.min_mrr:
            return 3
        return 0
    finally:
        await db.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure semantic search recall, MRR and latency")
    parser.add_argument("dataset", type=Path, help="JSONL evaluation dataset")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--min-mrr", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(parse_args())))
