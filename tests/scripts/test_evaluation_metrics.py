from __future__ import annotations

from uuid import uuid4

import pytest

from scripts.evaluate_search import percentile, result_matches
from src.models import SearchResult


def _result() -> SearchResult:
    return SearchResult(
        chunk_id=uuid4(),
        chunk_text="비밀번호는 12자 이상이며 다중 인증을 적용한다.",
        document_id=uuid4(),
        document_title="보안 정책 V2",
        version_number=2,
        page_number=3,
        section_title="인증",
        similarity=0.91,
    )


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([1, 2, 3, 4], 95) == 4
    assert percentile([], 95) == 0


def test_result_match_combines_declared_expectations() -> None:
    result = _result()
    assert result_matches(
        result,
        {"document_title": "보안 정책 V2", "text_contains": "다중 인증"},
    )
    assert not result_matches(result, {"document_title": "보안 정책 V1"})


def test_result_match_rejects_empty_expectation() -> None:
    with pytest.raises(ValueError, match="expected"):
        result_matches(_result(), {})
