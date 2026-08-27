# 개발자 B 상세 Phase 구현 계획 (Phase 5, 7, 8, 9)

## Phase 5: 임베딩 API 클라이언트 및 업로드 API 구현
**목표:** 외부 인터페이스와 API 모듈을 구성하여 문서를 받아들이고 임베딩 생성을 준비

1. **업로드 및 상태 확인 API (`src/api/routes.py`)**
   - **FastAPI 적용**: `UploadFile`을 활용해 파일을 수신하고, 메모리 혹은 임시 파일에 저장하여 SHA-256 해시를 계산.
   - **원자적 INSERT**: 동일 파일 중복 업로드 방지. 신규 파일일 경우 `documents`, `document_versions`, `change_log`에 3-테이블 Atomic INSERT 적용 (상태는 `PENDING`).
   - **진행률 API**: 개발자 B가 설계했던 `processing_status` 뷰를 쿼리하여 `(embedded_chunks / total_chunks * 100)` 형식의 진행률(Progress Bar 용도)을 클라이언트에 반환.

2. **임베딩 클라이언트 모듈 (`src/embedding/client.py`)**
   - **배치 처리 최적화**: 텍스트 청크를 설정된 크기로 묶어 로컬 Ollama API로 전송.
   - **안정성 확보**: `tenacity` 라이브러리를 활용하거나 직접 로직을 짜서 API 호출 실패에 대한 Retry 로직 구현.

---

## Phase 7: 임베딩 배치 저장 및 파이프라인 통합
**목표:** Worker 파이프라인의 끝단에서 벡터값을 빠르게 적재

1. **다중 데이터 배치 삽입 (`src/search/engine.py` 또는 `db.py` 헬퍼)**
   - 생성된 1024차원 벡터 데이터를 `doc_search.embeddings`에 벌크로 저장 (`executemany` 활용).
   - 삽입 후 해당 청크의 `is_embedded` 값을 일괄 `TRUE`로 업데이트.
2. **트랜잭션 정합성**
   - 위 배치 저장 로직을 하나의 BEGIN/COMMIT 블록으로 묶어, 서버가 중간에 꺼지더라도 벡터 데이터 일부만 저장되는 현상을 방지.

---

## Phase 8: 벡터 검색 엔진 및 MCP 서버 완성 (핵심 단계)
**목표:** AI Agent가 시스템 내 문서를 완벽하게 활용할 수 있도록 권한 부여

1. **벡터 검색 엔진 최적화 (`src/search/engine.py`)**
   - pgvector의 코사인 유사도(`<=>`) 연산자를 사용한 쿼리 작성.
   - 단순히 벡터 유사성만 비교하는 것이 아니라, 메타데이터 필터(특정 카테고리, 특정 날짜 이후 등)를 WHERE 절에 함께 적용하는 하이브리드 서치 로직 구현.
   - EXPLAIN ANALYZE로 쿼리 실행 속도를 모니터링하고 인덱스가 실제로 타는지 검증.

2. **MCP 서버 연동 (`mcp/server.py`)**
   - **4가지 MCP 도구(Tool) 구현**:
     1. `search_documents`: 사용자 질문의 임베딩을 만들어 DB에서 Top-K 문서 반환.
     2. `get_document`: 문서 메타데이터 제공.
     3. `list_documents`: 전체 문서 목차 제공.
     4. `get_chunk`: 특정 문서의 특정 위치 원문 제공.
   - **Claude Desktop 연동**: `mcp/server.py`를 stdio 기반 인터페이스로 띄우고 Claude 데스크탑 앱의 설정 파일에 등록하여 실제 채팅 환경에서 테스트.

---

## Phase 9: 통합 테스트 및 데모 시나리오 준비 (AI 중심)
**목표:** 공모전 심사위원에게 강렬한 인상을 남길 시연

1. **AI 자율 검색 시나리오 작성**
   - 심사위원에게 데모할 때: "우리 회사 작년 보안 가이드라인과 올해 가이드라인의 암호 정책 변경점을 알려줘" 라는 질문을 Claude에게 던짐.
   - Claude가 MCP를 통해 `search_documents`와 `get_chunk`를 스스로 호출하여 데이터를 비교하고 완벽한 답변을 구성하는 과정을 실시간 로깅 화면과 함께 시연.
2. **속도 및 정확도 체감**
   - 수천 장 분량의 샘플 PDF(매뉴얼, 규정집 등)를 미리 적재해 두고, 검색과 답변이 단 몇 초 안에 빠르고 정확하게 나오는 성능(pgvector HNSW 성능)을 증명.
