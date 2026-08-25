import pytest

from src.models import TextBlock
from src.pipeline.chunker import chunk_text


def test_chunk_text_respects_token_limit_and_overlap() -> None:
    text = "단어 " * 120
    blocks = [TextBlock(text=text, page=3, section="본문")]

    chunks = chunk_text(blocks, chunk_size=20, overlap=5)

    assert len(chunks) > 1
    assert all(chunk.content for chunk in chunks)
    assert all(chunk.page_number == 3 for chunk in chunks)
    assert all(chunk.section_title == "본문" for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    # 청크 간 토큰 overlap은 실제 임베딩 tokenizer 기준으로 검증한다.
    import tiktoken

    encoding = tiktoken.encoding_for_model("text-embedding-3-small")
    for previous, current in zip(chunks, chunks[1:]):
        previous_tokens = encoding.encode(previous.content)
        current_tokens = encoding.encode(current.content)
        assert previous_tokens[-5:] == current_tokens[:5]


def test_chunk_text_prefers_sentence_boundary_when_possible() -> None:
    text = "첫 번째 문장입니다. " + ("검색 품질을 검증합니다. " * 20)

    chunks = chunk_text([TextBlock(text=text)], chunk_size=30, overlap=5)

    assert len(chunks) > 1
    first = chunks[0]
    assert first.content.rstrip().endswith((".", "다."))


def test_chunk_text_tracks_relative_character_offsets() -> None:
    text = "alpha beta gamma delta epsilon"

    chunks = chunk_text([TextBlock(text=text)], chunk_size=3, overlap=1)

    assert all(chunk.content == text[chunk.char_start : chunk.char_end] for chunk in chunks)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)


def test_chunk_text_returns_empty_for_empty_blocks() -> None:
    assert chunk_text([TextBlock(text=""), TextBlock(text="   ")]) == []


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, 10), (10, 11), (-1, 0)],
)
def test_chunk_text_rejects_invalid_parameters(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text([TextBlock(text="text")], chunk_size=chunk_size, overlap=overlap)
