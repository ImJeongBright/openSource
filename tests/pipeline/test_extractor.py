from pathlib import Path

import pytest

from src.models import TextBlock
from src.pipeline.extractor import extract_text


def test_extract_txt_preserves_utf8_text_and_uses_common_model(tmp_path: Path) -> None:
    source = tmp_path / "manual.txt"
    source.write_text("OpenSQL 문서 검색\n한국어 텍스트", encoding="utf-8")

    blocks = extract_text(str(source), "txt")

    assert blocks == [
        TextBlock(text="OpenSQL 문서 검색\n한국어 텍스트", page=None, section=None)
    ]


def test_extract_empty_txt_returns_no_blocks(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    assert extract_text(str(source), "txt") == []


def test_extract_markdown_preserves_heading_context(tmp_path: Path) -> None:
    source = tmp_path / "guide.md"
    source.write_text(
        "# 설치 가이드\n\nOpenSQL을 설치합니다.\n\n"
        "## 접속 설정\n\n환경 변수를 설정합니다.\n",
        encoding="utf-8",
    )

    blocks = extract_text(str(source), "markdown")

    assert [(block.text, block.section) for block in blocks] == [
        ("OpenSQL을 설치합니다.", "설치 가이드"),
        ("환경 변수를 설정합니다.", "접속 설정"),
    ]
    assert all(block.page is None for block in blocks)


def test_extract_rejects_unknown_file_type(tmp_path: Path) -> None:
    source = tmp_path / "manual.csv"
    source.write_text("a,b\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(str(source), "csv")


def test_extract_rejects_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        extract_text("/tmp/opensql-phase4-file-does-not-exist.pdf", "pdf")


def test_extract_pdf_returns_one_based_page_numbers(tmp_path: Path) -> None:
    fitz = pytest.importorskip(
        "fitz", reason="PyMuPDF is required for PDF extraction tests"
    )
    source = tmp_path / "manual.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "first page")
    page = document.new_page()
    page.insert_text((72, 72), "second page")
    document.save(source)
    document.close()

    blocks = extract_text(str(source), "pdf")

    assert [(block.text, block.page) for block in blocks] == [
        ("first page", 1),
        ("second page", 2),
    ]


def test_extract_pdf_skips_textless_pages(tmp_path: Path) -> None:
    fitz = pytest.importorskip(
        "fitz", reason="PyMuPDF is required for PDF extraction tests"
    )
    source = tmp_path / "scanned.pdf"
    document = fitz.open()
    document.new_page()  # 이미지 전용 페이지를 대신하는 빈 페이지
    page = document.new_page()
    page.insert_text((72, 72), "searchable page")
    document.save(source)
    document.close()

    blocks = extract_text(str(source), "pdf")

    assert [(block.text, block.page) for block in blocks] == [("searchable page", 2)]
