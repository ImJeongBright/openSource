from typing import List
from src.models import TextBlock, ChunkData
from src.config import settings

def chunk_text(blocks: List[TextBlock], chunk_size: int = settings.CHUNK_SIZE, overlap: int = settings.CHUNK_OVERLAP) -> List[ChunkData]:
    """
    [개발자 A 구현] 텍스트 블록 리스트를 받아 토큰 기반 슬라이딩 윈도우 방식으로 청킹합니다.
    """
    raise NotImplementedError("Phase 4에서 개발자 A가 구현할 예정입니다.")
