# OpenSQL AI 문서 검색 시스템

OpenSQL(PostgreSQL 17.8 기반) + pgvector를 활용한 기업용 AI 문서 검색 및 버전 관리 시스템.

## 프로젝트 개요

PDF, TXT, Markdown 문서를 업로드하면 텍스트 추출 → 청킹 → 임베딩을 자동 수행하고,
OpenSQL에 문서 메타데이터, 버전, 청크, 임베딩 벡터, 변경 로그를 통합 저장한다.
MCP(Model Context Protocol) 인터페이스를 통해 Claude, GPT 등 AI 도구가 의미 검색을 재사용할 수 있다.

## 주요 특징

- **단일 DB 통합**: 벡터 DB와 RDBMS를 별도 운영하지 않고 pgvector로 통합
- **Atomic 버전 전환**: 새 버전 임베딩 완료 전까지 기존 버전 유지
- **장애 복구**: 변경 로그 기반 재시도, 멱등성 보장
- **추적 가능한 검색**: 문서명, 버전, 페이지, 근거 문단을 포함한 결과 반환
- **고가용성**: Patroni + etcd 기반 OpenSQL HA 구조 활용

## 디렉터리 구조

```
opensql-doc-search/
├── docs/          # 설계 문서
├── sql/           # DB 스키마 DDL
├── src/           # 파이프라인 및 API 소스코드
├── mcp/           # MCP 서버
├── scripts/       # 평가·부하·데모 실행 도구
├── samples/       # 비밀정보가 없는 데모 문서
└── .env.example   # 환경변수 예시
```

## 설계 문서

- [프로젝트 정의서](docs/01_project_definition.md)
- [유스케이스 명세서](docs/02_usecase_specification.md)
- [기능 요구사항 명세서](docs/03_functional_requirements.md)
- [비기능 요구사항 명세서](docs/04_non_functional_requirements.md)
- [DB 스키마 설계](docs/05_database_schema.md)
- [데이터 파이프라인 설계](docs/06_data_pipeline.md)
- [운영 및 MCP 연결](docs/12_operations_and_mcp_setup.md)

## 현재 구현 상태

- Qwen3 Embedding 0.6B를 Ollama에서 로컬 구동하고 1024차원 벡터를 저장합니다.
- 업로드, 버전 추가, 상태·목록·상세·삭제 API를 제공합니다.
- ACTIVE 버전만 대상으로 제목·문서·카테고리·태그·날짜 필터 검색을 수행합니다.
- MCP stdio 서버가 `search_documents`, `get_document`, `list_documents`, `get_chunk`를 제공합니다.
- 검색 품질, 동시 요청 성능, 실제 실행계획과 Phase 9 데모를 반복 실행할 수 있습니다.

```bash
make test
make lint
make evaluate DATASET=tests/fixtures/search_quality.example.jsonl
python scripts/demo_phase9.py --cleanup
```

## 기술 스택

| 영역 | 기술 |
|------|------|
| DB | Tmax OpenSQL (PostgreSQL 17.8) |
| 벡터 검색 | pgvector 0.8.1 (HNSW) |
| HA | Patroni 4.0.5 + etcd 3.6.5 |
| 언어 | Python 3.10+ |
| 인터페이스 | MCP (Model Context Protocol) |
| 임베딩 | Qwen3 Embedding 0.6B + Ollama (1024차원) |
