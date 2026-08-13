from typing import List
from src.models import TextBlock

def extract_text(file_path: str, file_type: str) -> List[TextBlock]:
    """
    [개발자 A 구현] 파일 경로와 타입을 받아 텍스트 블록 리스트를 반환합니다.
    - PDF: pymupdf (fitz) 사용
    - TXT: 기본 파일 읽기
    - Markdown: mistune 파서 사용
    """
    raise NotImplementedError("Phase 4에서 개발자 A가 구현할 예정입니다.")
