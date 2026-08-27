# DocuTrace

**OpenSQL 기반 AI 문서 동기화 및 MCP 검색 플랫폼**

PDF, TXT, Markdown 문서를 업로드하면 텍스트 추출·청킹·임베딩을 비동기로 처리하고,
문서 원본과 버전, 청크, 벡터, 처리 로그를 OpenSQL에 통합 저장한다. 검색 결과에는
페이지·섹션·청크 ID를 함께 제공하며, MCP(Model Context Protocol)를 통해 AI 클라이언트가
검색 결과와 원문 근거를 사용할 수 있다.

## 프로젝트 정보

| 항목 | 내용 |
|---|---|
| 프로젝트명 | **DocuTrace : OpenSQL 기반 AI 문서 동기화 및 MCP 검색 플랫폼** |
| 팀명 | 투맨프로젝트 |
| 팀 인원 | 2명 |
| 참가부문 | 학생 |
| 과제유형 | 지정과제(티맥스티베로) |
| 저장소 | [GitHub](https://github.com/ImJeongBright/openSource) |
| 시연영상 | [YouTube](https://youtu.be/Yz8xGcG9i5k) |

## 핵심 기능

| 영역 | 제공 기능 |
|---|---|
| 문서 관리 | PDF/TXT/Markdown 업로드, 최대 100MB 스트리밍 저장, SHA-256 중복 감지 |
| 버전 관리 | 신규 버전 처리, `PENDING → PROCESSING → ACTIVE` 상태 전이, 원자적 활성화 |
| 문서 처리 | PDF 페이지·Markdown 섹션 보존, 토큰 기반 청킹, 배치 임베딩 |
| 장애 복구 | `SKIP LOCKED`, lease·heartbeat, sweeper, retry/backoff, 멱등성 |
| 검색 | pgvector cosine similarity, 메타데이터 필터, 출처 정보 반환 |
| AI 연동 | MCP stdio 서버와 `search_documents`, `get_document`, `list_documents`, `get_chunk` 제공 |

## 처리 흐름

```text
파일 업로드
    ↓
FastAPI: 파일 검증·해시 계산·문서/버전 등록
    ↓
OpenSQL: PENDING 작업 저장
    ↓
Worker: 추출 → 청킹 → Qwen3 임베딩 → 멱등적 저장
    ↓
원자적 버전 전환: 기존 ACTIVE → ARCHIVED, 신규 버전 → ACTIVE
    ↓
REST API 또는 MCP 검색
```

새 버전의 처리가 끝나기 전에는 기존 `ACTIVE` 버전을 유지하므로, 불완전한 데이터가
검색 결과에 노출되지 않는다.

## 기술 스택

| 영역 | 기술 |
|---|---|
| 데이터베이스 | Tmax OpenSQL 3.17.8.7, PostgreSQL 17.8 기반 |
| 벡터 검색 | pgvector 0.8.1, cosine distance, HNSW |
| 임베딩 | Qwen3 Embedding 0.6B, Ollama, 1024차원 |
| API | FastAPI, Uvicorn, asyncpg |
| 문서 처리 | PyMuPDF, mistune, tiktoken |
| AI 인터페이스 | MCP Python SDK, stdio transport |
| 운영 | Patroni, etcd, systemd |
| 테스트 | pytest, pytest-asyncio, Ruff |

## 디렉터리 구조

```text
opensql-doc-search/
├── docs/                  # 설계·운영·협업 문서
├── sql/                   # 스키마, 함수, 뷰, 마이그레이션
├── src/
│   ├── api/               # 문서 업로드·조회·삭제 API
│   ├── embedding/         # 임베딩 생성·저장
│   ├── pipeline/          # 추출·청킹·버전 전환
│   ├── search/            # 벡터 검색
│   └── worker/            # 비동기 처리 Worker
├── mcp/                   # MCP stdio 서버
├── scripts/               # 평가·벤치마크·통합 데모
├── tests/                 # 단위·통합 테스트
├── samples/               # 데모 문서
├── .env.example           # 환경변수 예시
└── Makefile               # 설치·실행·검증 명령
```

## 빠른 시작

### 1. 환경 구성

Python 3.10 이상, OpenSQL, Ollama가 필요하다. 상세한 OpenSQL과 운영 환경 설정은
[OpenSQL 매뉴얼](docs/opensql_manual.md)과 [운영·MCP 연결 가이드](docs/12_operations_and_mcp_setup.md)를
참고한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make env
```

`.env`에 DB 접속 정보와 Ollama 설정을 입력한 뒤, Ollama에
`qwen3-embedding:0.6b` 모델을 준비한다.

### 2. 데이터베이스 초기화

```bash
make init-db
```

초기화 전에 `.env`의 `OPENSQL_*` 설정이 올바른지 확인한다.

### 3. 서비스 실행

각 명령을 별도 터미널에서 실행한다.

```bash
make run-api
make run-worker
make run-mcp
```

API 상태와 MCP 도구 목록은 다음처럼 확인할 수 있다.

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
.venv/bin/python mcp/server.py --check
```

## API와 MCP

주요 REST API는 다음과 같다.

- `POST /api/documents` — 문서 업로드 및 버전 등록
- `GET /api/documents` — 문서 목록과 필터 조회
- `GET /api/documents/{id}` — 문서 상세 및 버전 이력
- `GET /api/documents/{id}/status` — 처리 상태와 진행률 조회
- `DELETE /api/documents/{id}` — 문서 삭제 및 DELETE 이벤트 기록

MCP 서버는 다음 네 가지 도구를 stdio로 제공한다.

- `search_documents`
- `get_document`
- `list_documents`
- `get_chunk`

Claude Desktop 연결 예시는 [MCP 설정 예시](docs/examples/claude_desktop_config.example.json)에서
확인할 수 있다.

## 테스트와 평가

```bash
make test
make lint
make evaluate DATASET=tests/fixtures/search_quality.example.jsonl
make benchmark DATASET=tests/fixtures/search_quality.example.jsonl
python scripts/demo_phase9.py --cleanup --output /tmp/phase9-demo-result.json
```

Phase 9 데모는 보안 정책 V1·V2를 업로드하고, V2 처리 중 기존 ACTIVE 버전이 유지되는지와
MCP 검색 결과의 출처 추적이 가능한지를 검증한다.

## 문서

- [전체 문서 목록](docs/README.md)
- [프로젝트 정의서](docs/01_project_definition.md)
- [데이터 파이프라인 설계](docs/06_data_pipeline.md)
- [2인 협업 구현 가이드](docs/08_team_implementation_guide.md)
- [운영 및 MCP 연결 가이드](docs/12_operations_and_mcp_setup.md)

## 라이선스

직접 작성한 소스코드는 [MIT License](LICENSE)로 배포한다.

외부 의존성은 각 프로젝트의 라이선스와 사용 조건을 따른다. 특히 PyMuPDF는 AGPL 또는
상용 라이선스 조건을, OpenSQL과 Qwen3 Embedding은 각 공식 배포 조건을 확인해야 한다.
