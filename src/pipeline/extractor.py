import logging
from pathlib import Path
from typing import Any, Iterable, List

import fitz
import mistune

from src.models import TextBlock


logger = logging.getLogger(__name__)


_FILE_TYPE_ALIASES = {
    "md": "markdown",
    "markdown": "markdown",
    "txt": "txt",
    "text": "txt",
    "pdf": "pdf",
}


def _normalize_file_type(file_type: str) -> str:
    normalized = file_type.lower().strip().lstrip(".")
    try:
        return _FILE_TYPE_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported file type: {file_type}") from exc


def _node_text(node: Any) -> str:
    """AST 노드에서 실제 텍스트를 재귀적으로 꺼낸다."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("raw"), str):
        return node["raw"]
    children = node.get("children", [])
    return "".join(_node_text(child) for child in children)


def _extract_markdown(source: str) -> List[TextBlock]:
    parser = mistune.create_markdown(renderer="ast")
    nodes: Iterable[dict[str, Any]] = parser(source)
    blocks: List[TextBlock] = []
    current_section = None

    for node in nodes:
        node_type = node.get("type")
        if node_type == "heading":
            heading = _node_text(node).strip()
            if heading:
                current_section = heading
            continue
        if node_type in {"blank_line", "thematic_break"}:
            continue

        text = _node_text(node).strip()
        if text:
            blocks.append(TextBlock(text=text, section=current_section))

    return blocks


def _extract_pdf(path: Path) -> List[TextBlock]:
    blocks: List[TextBlock] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            # 이미지 전용 페이지는 OCR 범위가 아니므로 검색 텍스트로 저장하지 않는다.
            if text:
                blocks.append(TextBlock(text=text, page=page_number))
            else:
                logger.info("Skipping textless PDF page: path=%s page=%d", path, page_number)
    return blocks


def extract_text(file_path: str, file_type: str) -> List[TextBlock]:
    """
    [개발자 A 구현] 파일 경로와 타입을 받아 텍스트 블록 리스트를 반환합니다.
    - PDF: pymupdf (fitz) 사용
    - TXT: 기본 파일 읽기
    - Markdown: mistune 파서 사용
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(file_path)

    normalized_type = _normalize_file_type(file_type)
    if normalized_type == "txt":
        text = path.read_text(encoding="utf-8")
        return [TextBlock(text=text)] if text.strip() else []
    if normalized_type == "markdown":
        return _extract_markdown(path.read_text(encoding="utf-8"))
    return _extract_pdf(path)
