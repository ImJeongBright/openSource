from typing import List
from src.models import SearchResult

async def search(query: str, top_k: int = 5, category: str = None) -> List[SearchResult]:
    """
    [개발자 B 구현] 검색 쿼리를 임베딩한 후 DB의 search_documents 함수를 호출하여 결과를 반환합니다.
    """
    raise NotImplementedError("Phase 8에서 개발자 B가 구현할 예정입니다.")
