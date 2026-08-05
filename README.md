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
└── .env.example   # 환경변수 예시
```

## 설계 문서

- [프로젝트 정의서](docs/01_project_definition.md)
- [유스케이스 명세서](docs/02_usecase_specification.md)
- [기능 요구사항 명세서](docs/03_functional_requirements.md)
- [비기능 요구사항 명세서](docs/04_non_functional_requirements.md)
- [DB 스키마 설계](docs/05_database_schema.md)
- [데이터 파이프라인 설계](docs/06_data_pipeline.md)

## 기술 스택

| 영역 | 기술 |
|------|------|
| DB | Tmax OpenSQL (PostgreSQL 17.8) |
| 벡터 검색 | pgvector 0.8.1 (HNSW) |
| HA | Patroni 4.0.5 + etcd 3.6.5 |
| 언어 | Python 3.10+ |
| 인터페이스 | MCP (Model Context Protocol) |
