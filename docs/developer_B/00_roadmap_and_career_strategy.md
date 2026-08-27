# 개발자 B (DB App & Interface Engineer) 로드맵 및 커리어 전략

## 1. 개요 및 공모전 우승 전략
개발자 B는 OpenSQL에 적재된 데이터를 AI가 쉽게 이해하고 검색할 수 있도록 API를 제공하고, MCP(Model Context Protocol) 기반 서버를 구축하는 역할을 담당합니다.
공모전 우승을 위해서는 **"최신 AI 트렌드(MCP)의 완벽한 적용과 빠르고 정확한 AI 검색(RAG) 파이프라인"**을 어필하는 것이 핵심입니다.

### 🏆 공모전 심사위원 어필 포인트
* **MCP (Model Context Protocol)의 선도적 도입**: AI Agent(Claude Desktop 등)가 로컬 DB에 직접 접근하여 마치 사람처럼 문서를 검색하고 열람하는 과정을 시연하여 높은 혁신 점수 획득.
* **고성능 하이브리드 RAG 검색**: pgvector의 HNSW 인덱스를 활용한 빠르고 정확한 코사인 유사도 검색과 메타데이터 필터링 결합.
* **사용자 친화적 API 설계**: 대용량 문서를 비동기로 업로드하고, 진행률(`processing_status` 뷰 연동)을 클라이언트에 실시간으로 보여주는 UX 고려 설계.

---

## 2. 채용 공고 및 이력서(Portfolio) 활용 역량
이 프로젝트를 통해 개발자 B는 다음의 역량을 이력서에 강력하게 어필할 수 있습니다.

* **LLM 및 AI Agent 연동 엔지니어링 역량**: 
  - MCP (Model Context Protocol) 서버 생태계 이해 및 커스텀 Tool(검색, 문서 열람) 구현 경험.
  - 로컬 오픈웨이트 임베딩과 OpenSQL pgvector를 결합한 RAG 시스템 구축.
* **OpenSQL을 활용한 Vector Search 경험**: 
  - `pgvector`를 직접 활용하여 임베딩을 관리하고, `EXPLAIN ANALYZE`를 통한 검색 쿼리 성능 프로파일링.
* **비동기 API 서비스 아키텍처**:
  - FastAPI와 asyncpg를 활용한 고성능 Non-blocking API 서버 개발 경험.
  - Ollama HTTP 호출에 대한 Retry/Backoff 및 오류 핸들링.

---

## 3. 남은 Phase 로드맵 (Phase 5, 7, 8, 9)

* **Phase 5: 임베딩 API 클라이언트 및 업로드 API 구현**
  - **핵심 목표**: 사용자의 파일 업로드를 받고, Qwen3 Embedding 연동 모듈을 구축합니다.
  - **마일스톤**: SHA-256 중복 감지를 포함한 업로드 API, 재시도 로직이 포함된 Ollama 클라이언트.

* **Phase 7: 임베딩 배치 저장 및 파이프라인 통합 (A와 협업)**
  - **핵심 목표**: 생성된 수많은 벡터 값을 OpenSQL에 배치(Batch)로 빠르게 꽂아넣습니다.
  - **마일스톤**: `chunks` 테이블 업데이트 및 `embeddings` 테이블 INSERT 트랜잭션 최적화.

* **Phase 8: 벡터 검색 엔진 및 MCP 서버 완성 (주도)**
  - **핵심 목표**: AI Agent가 사용할 수 있는 검색 쿼리와 4개의 핵심 MCP Tool을 개발합니다.
  - **마일스톤**: pgvector 쿼리, `mcp/server.py` 완성 및 Claude Desktop 연동 테스트 성공.

* **Phase 9: 통합 테스트 및 데모(RAG 및 AI 활용) 시나리오 준비**
  - **핵심 목표**: AI(Claude)에게 복잡한 질문을 던졌을 때 MCP를 통해 우리 DB를 스스로 뒤져서 완벽한 답변을 생성하는 과정을 멋지게 시연합니다.
