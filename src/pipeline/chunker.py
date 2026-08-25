from bisect import bisect_right
import re
from typing import List, Optional, Sequence

import tiktoken

from src.models import TextBlock, ChunkData
from src.config import settings


_BOUNDARY_PATTERN = re.compile(r"(?:[.!?。！？]|\n)")


def _encoding() -> tiktoken.Encoding:
    return tiktoken.encoding_for_model("text-embedding-3-small")


def _choose_end(
    text: str,
    token_starts: Sequence[int],
    start: int,
    target_end: int,
    chunk_size: int,
) -> int:
    """가능하면 목표 토큰 지점 이전의 문장/줄바꿈 경계를 선택한다."""
    start_char = token_starts[start]
    target_char = token_starts[target_end]
    if target_char <= start_char:
        return target_end

    minimum_end = start + max(1, min(chunk_size // 2, target_end - start))
    candidate_text = text[start_char:target_char]
    for match in reversed(list(_BOUNDARY_PATTERN.finditer(candidate_text))):
        boundary_char = start_char + match.end()
        candidate_end = bisect_right(token_starts, boundary_char) - 1
        if minimum_end <= candidate_end <= target_end:
            return candidate_end
    return target_end


def chunk_text(
    blocks: List[TextBlock],
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
) -> List[ChunkData]:
    """
    [개발자 A 구현] 텍스트 블록 리스트를 받아 토큰 기반 슬라이딩 윈도우 방식으로 청킹합니다.
    """
    chunk_size = settings.CHUNK_SIZE if chunk_size is None else chunk_size
    overlap = settings.CHUNK_OVERLAP if overlap is None else overlap
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")

    encoding = _encoding()
    chunks: List[ChunkData] = []
    chunk_index = 0

    for block in blocks:
        if not block.text.strip():
            continue

        tokens = encoding.encode(block.text)
        decoded_text, token_offsets = encoding.decode_with_offsets(tokens)
        token_starts = list(token_offsets) + [len(decoded_text)]
        start = 0

        while start < len(tokens):
            target_end = min(start + chunk_size, len(tokens))
            end = _choose_end(
                decoded_text, token_starts, start, target_end, chunk_size
            )
            if end <= start:
                end = target_end
            start_char = token_starts[start]
            end_char = token_starts[end]
            if end_char <= start_char and end < len(tokens):
                end += 1
                end_char = token_starts[end]

            content = decoded_text[start_char:end_char]
            if content:
                chunks.append(
                    ChunkData(
                        index=chunk_index,
                        content=content,
                        page_number=block.page,
                        section_title=block.section,
                        char_start=start_char,
                        char_end=end_char,
                    )
                )
                chunk_index += 1

            if end >= len(tokens):
                break
            start = max(end - overlap, start + 1)

    return chunks
