# OpenSQL AI 문서 검색 시스템 — 2인 협업 구현 가이드

본 문서는 `opensql-doc-search` 프로젝트를 2인 개발자가 협업하여 구현하기 위한  
**역할 분담(R&R)** 및 **단계별 구현 프로세스(Phase 1~9)** 가이드입니다.

---

## 1. 역할 분담 (R&R)

전체 시스템은 **데이터 적재 축**과 **검색·인터페이스 축**으로 나뉩니다.  
두 개발자 모두 OpenSQL을 직접 다루되, **담당하는 SQL 영역을 특성별로 분리**합니다.

### OpenSQL 경험 분배 기준

| OpenSQL 경험 항목 | 개발자 A | 개발자 B |
|---|:---:|:---:|
| Extension / Schema 초기화 | ✅ | |
| ENUM 타입 정의 | ✅ | |
| 코어 엔티티 테이블 DDL (`documents`, `document_versions`, `chunks`) | ✅ | |
| 적재 테이블 DDL (`embeddings`, `change_log`, `embedding_models`) | | ✅ |
| HNSW 인덱스 생성 (pgvector) | ✅ | |
| 저장 함수 작성 (`activate_version`) | ✅ | |
| 뷰 설계 (`processing_status`, `active_document_chunks`) | | ✅ |
| 시드 데이터 INSERT | | ✅ |
| `SELECT FOR UPDATE SKIP LOCKED` (Worker 잠금) | ✅ | |
| 3-테이블 Atomic INSERT (업로드 등록) | | ✅ |
| 배치 임베딩 저장 트랜잭션 | | ✅ |
| pgvector 코사인 유사도 검색 쿼리 | | ✅ |
| `EXPLAIN ANALYZE` 쿼리 성능 분석 | ✅ | ✅ |
| Patroni HA / Failover 복구 | ✅ | |

---

### 🧑‍💻 개발자 A — DB Core & Pipeline Engineer

> **한 줄 요약**: "DB의 뼈대(스키마·함수·Worker)를 만들고, 문서가 안전하게 적재되도록 한다"

#### 담당 도메인
- **DB 코어 설계**: Extension·Schema·ENUM 타입, 코어 엔티티 테이블 3종, HNSW 인덱스, `activate_version()` 저장 함수
- **데이터 파이프라인**: 텍스트 추출(PDF/TXT/MD), 청킹 알고리즘, Atomic 버전 전환
- **Worker 인프라**: `change_log` 기반 비동기 Worker 데몬, `SELECT FOR UPDATE SKIP LOCKED`, 재시도·멱등성·타임아웃 복구
- **HA 운영**: Patroni + etcd 상태 관리, Failover 후 재처리 흐름 검증

#### 핵심 모듈 및 산출물

| 산출물 | 설명 |
|--------|------|
| `sql/01_extensions.sql` | Extension, Schema 초기화 |
| `sql/02_types.sql` | ENUM 타입 3종 |
| `sql/03_core_tables.sql` | `documents`, `document_versions`, `chunks` DDL |
| `sql/04_indexes.sql` | B-Tree + HNSW 인덱스 |
| `sql/05_functions.sql` | `activate_version()` 저장 함수 |
| `src/pipeline/extractor.py` | PDF/TXT/MD 텍스트 추출기 |
| `src/pipeline/chunker.py` | 슬라이딩 윈도우 청킹 함수 |
| `src/pipeline/versioner.py` | Atomic 버전 전환 래퍼 |
| `src/worker/daemon.py` | 비동기 Worker 루프 + 상태 전이 |
| `opensql-worker.service` | systemd 유닛 파일 |

#### 담당 Phase
`Phase 1 (DB 인프라)` → `Phase 2 A (코어 DDL)` → `Phase 4 (추출·청킹)` → `Phase 6 (Worker)` → `Phase 7 A측 (versioner + 파이프라인 통합)`

#### 필요 역량
- PostgreSQL / OpenSQL 운영 경험 (DDL, 트랜잭션, 행 잠금 메커니즘)
- pgvector 인덱스 설계 (HNSW 파라미터), Patroni + etcd 기본 이해
- Python `asyncpg` 비동기 DB 처리
- PDF 파싱 (`PyMuPDF`), Markdown 파싱 (`mistune`)

---

### 🧑‍💻 개발자 B — DB Application & Interface Engineer

> **한 줄 요약**: "적재된 데이터를 검색하고, 사용자·AI가 시스템과 소통하도록 한다"

#### 담당 도메인
- **DB 애플리케이션 설계**: 적재 테이블 3종 DDL, 운영 뷰 2종 설계, 시드 데이터 관리
- **업로드 API**: 파일 수신·검증, SHA-256 중복 감지, 3-테이블 Atomic INSERT 트랜잭션
- **임베딩 연동**: OpenAI 임베딩 API 클라이언트, 배치 저장 트랜잭션, Retry/Backoff
- **검색 엔진**: pgvector ANN 쿼리, 메타데이터 필터, `EXPLAIN ANALYZE` 성능 분석
- **MCP 서버**: 4개 Tool 구현 및 AI 클라이언트 연동

#### 핵심 모듈 및 산출물

| 산출물 | 설명 |
|--------|------|
| `sql/06_app_tables.sql` | `embeddings`, `change_log`, `embedding_models` DDL |
| `sql/07_views.sql` | `processing_status`, `active_document_chunks` 뷰 |
| `sql/08_seed.sql` | 초기 임베딩 모델 레코드 시드 |
| `src/db.py` | asyncpg 커넥션 풀 (A와 공유) |
| `src/models.py` | Pydantic 공유 데이터 모델 (A와 공유) |
| `src/embedding/client.py` | OpenAI 임베딩 API 클라이언트 |
| `src/api/routes.py` | 업로드·조회·상태 REST API |
| `src/search/engine.py` | HNSW 벡터 검색 + 필터 쿼리 + 성능 분석 |
| `mcp/server.py` | MCP 서버 (`search_documents` 등 4 Tool) |

#### 담당 Phase
`Phase 1 (Python 환경)` → `Phase 2 B (적재 DDL·뷰·시드)` → `Phase 3 (인터페이스 합의)` → `Phase 5 (임베딩·업로드 API)` → `Phase 7 B측 (배치 저장)` → `Phase 8 (검색·MCP)`

#### 필요 역량
- PostgreSQL DDL 작성 및 뷰 설계 경험
- 복합 트랜잭션 작성 (다중 테이블 INSERT/UPDATE)
- pgvector 검색 쿼리 작성 및 `EXPLAIN ANALYZE` 해석
- FastAPI / Python async 서버 개발
- OpenAI Embeddings API 연동, MCP (Model Context Protocol) 기본 이해

---

### 🤝 공유 책임

| 항목 | 설명 |
|------|------|
| `src/models.py` | 두 개발자가 함께 합의하는 공유 데이터 모델 |
| `.env` 구성 | DB 접속 정보, API 키, 파라미터 값 공동 관리 |
| 함수 시그니처 | Phase 3에서 합의 후 단독 수정 불가 |
| E2E 테스트 | Phase 9 전 과정 함께 실행 및 결과 공유 |
| 데모 준비 | `docs/07_demo_scenario.md` 기반 시연 환경 공동 구성 |

---

## 2. 구현 단계 전체 개요

```
Phase 1  ─  개발 환경 구성 및 기반 인프라 세팅
Phase 2  ─  DB 스키마 설계 검증 및 DDL 적용
Phase 3  ─  공유 인터페이스 및 프로젝트 뼈대 정의
Phase 4  ─  텍스트 추출 및 청킹(Chunking) 구현
Phase 5  ─  임베딩 API 클라이언트 및 업로드 API 구현
Phase 6  ─  비동기 Worker 데몬 구현
Phase 7  ─  Atomic 버전 전환 및 파이프라인 통합
Phase 8  ─  벡터 검색 엔진 및 MCP 서버 완성
Phase 9  ─  통합 테스트 및 데모 시나리오 준비
```

---

## 3. 단계별 구현 프로세스

---

### Phase 1 — 개발 환경 구성 및 기반 인프라 세팅

**목표**: 두 개발자의 개발 환경을 동기화하고, OpenSQL 및 pgvector가 정상 동작함을 확인한다.

#### 개발자 A
- [ ] Rocky Linux 9 환경에 OpenSQL 3.17.8.7 설치·기동 확인
- [ ] pgvector 0.8.1 확장 활성화 (`CREATE EXTENSION IF NOT EXISTS vector;`)
- [ ] uuid-ossp 확장 활성화
- [ ] `doc_search` 전용 DB 사용자 및 Role 생성
- [ ] Patroni + etcd HA 환경 확인 (`curl http://localhost:8008/cluster`)

#### 개발자 B
- [ ] Python 3.10+ 가상 환경 구성
- [ ] 공통 의존성 패키지 목록 작성 및 `requirements.txt` 초안 작성
  - `asyncpg`, `pgvector`, `openai`, `pymupdf`, `mistune`, `fastapi`, `uvicorn`
- [ ] `.env.example` 기반으로 팀 공용 `.env` 파일 구성 (DB 접속 정보, API 키 등)
- [ ] 공통 DB 연결 헬퍼(`src/db.py`) 스켈레톤 작성 (asyncpg connection pool)

#### 체크포인트
```bash
# A: pgvector 설치 확인
psql -U app_user -d doc_search -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"

# B: DB 접속 확인
python -c "import asyncpg; print('asyncpg OK')"
```

---

### Phase 2 — DB 스키마 설계 및 DDL 적용 (A·B 분담)

**목표**: `docs/05_database_schema.md`를 기준으로 DDL을 **테이블 특성별로 분담**하여 두 개발자 모두 OpenSQL DDL 작성을 직접 경험한다.

#### 개발자 A — 코어 엔티티 / 인덱스 / 저장 함수
- [ ] `sql/01_extensions.sql` — `vector`, `uuid-ossp` Extension 및 `doc_search` Schema 생성
- [ ] `sql/02_types.sql` — ENUM 타입 3종 생성
  - `version_status` (PENDING / PROCESSING / ACTIVE / ARCHIVED / FAILED)
  - `log_event_type` (UPLOAD / UPDATE / DELETE / EMBED_START 등)
  - `log_status` (PENDING / PROCESSING / COMPLETED / DEAD_LETTER)
- [ ] `sql/03_core_tables.sql` — 코어 엔티티 테이블 3종 DDL
  - `documents` (문서 원본 정보)
  - `document_versions` (버전별 메타데이터·상태)
  - `chunks` (청크 텍스트·위치·임베딩 완료 여부)
- [ ] `sql/04_indexes.sql` — B-Tree 인덱스 + HNSW 인덱스 생성
  ```sql
  -- pgvector HNSW 인덱스 (코사인 유사도 기준)
  CREATE INDEX idx_embeddings_hnsw ON doc_search.embeddings
  USING hnsw (vector vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
  ```
- [ ] `sql/05_functions.sql` — `activate_version(version_id UUID)` 저장 함수 작성
  - 단일 트랜잭션 내에서 기존 ACTIVE → ARCHIVED, 신규 → ACTIVE 원자적 처리

#### 개발자 B — 적재 테이블 / 뷰 / 시드 데이터
- [ ] `sql/06_app_tables.sql` — 적재·운영 테이블 3종 DDL
  - `embedding_models` (임베딩 모델 레지스트리)
  - `embeddings` (벡터 저장, `chunk_id UNIQUE` 제약)
  - `change_log` (이벤트 로그, `worker_id`, `locked_at`, `retry_count`)
- [ ] `sql/07_views.sql` — 운영 뷰 2종 작성
  - `processing_status` : 문서별 처리 진행률 (`embedded_chunks / total_chunks * 100`)
  - `active_document_chunks` : ACTIVE 버전 청크 + 벡터 조인 뷰 (검색용)
- [ ] `sql/08_seed.sql` — 초기 임베딩 모델 레코드 INSERT
  ```sql
  INSERT INTO doc_search.embedding_models (model_name, model_version, provider, dimensions)
  VALUES ('text-embedding-3-small', '2024-01-01', 'openai', 1536);
  ```
- [ ] A가 작성한 DDL 리뷰: 컬럼명·타입·제약조건이 API 응답 구조와 일치하는지 확인

#### 체크포인트 (A & B 함께)
```sql
-- 테이블 8개 모두 존재 확인
SELECT tablename FROM pg_tables WHERE schemaname = 'doc_search' ORDER BY tablename;

-- HNSW 인덱스 확인
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'embeddings';

-- 뷰 2개 확인
SELECT viewname FROM pg_views WHERE schemaname = 'doc_search';

-- 시드 데이터 확인
SELECT model_name, dimensions FROM doc_search.embedding_models;
```

---

### Phase 3 — 공유 인터페이스 및 프로젝트 뼈대 정의

**목표**: 두 개발자가 독립적으로 모듈을 개발할 수 있도록 함수 시그니처와 데이터 모델을 먼저 합의한다.

> **이 단계는 두 개발자가 함께 진행하는 합의 단계입니다.**

#### 공통 작업 (A & B 함께)
- [ ] `src/models.py` — 공유 Pydantic 데이터 모델 정의
  - 현재 모델: `DocumentMeta`, `VersionInfo`, `TextBlock`, `ChunkData`, `SearchResult`
  - DB 작업 레코드 모델(`EmbeddingRecord`, `ChangeLogEntry`)이 필요해지면 Phase 3 합의 후 별도 추가한다.
- [ ] 파이프라인 모듈 간 함수 시그니처 합의 및 문서화
  ```python
  # 개발자 A가 구현하고 B가 사용하는 인터페이스
  def extract_text(file_path: str, file_type: str) -> list[TextBlock]: ...
  def chunk_text(blocks: list[TextBlock], chunk_size=512, overlap=50) -> list[ChunkData]: ...

  # 개발자 B가 구현하고 A가 사용하는 인터페이스
  async def generate_embeddings(texts: list[str]) -> list[list[float]]: ...
  # 배치 저장 시그니처는 Phase 7에서 DB 트랜잭션 경계와 함께 확정한다.
  ```
- [ ] `src/` 디렉터리 구조 합의 및 생성
  ```
  src/
  ├── models.py        # 공유 데이터 모델 (A & B)
  ├── db.py            # DB 연결 풀 (B 초안, A 검토)
  ├── pipeline/
  │   ├── extractor.py # 텍스트 추출 (A)
  │   ├── chunker.py   # 청킹 (A)
  │   └── versioner.py # 버전 전환 (A)
  ├── embedding/
  │   └── client.py    # 임베딩 API 클라이언트 (B)
  ├── worker/
  │   └── daemon.py    # 비동기 Worker (A)
  └── api/
      └── routes.py    # REST API (B)
  ```

---

### Phase 4 — 텍스트 추출 및 청킹(Chunking) 구현

**목표**: 업로드된 파일에서 텍스트와 구조 정보를 추출하고 검색 단위인 청크로 분할한다.

#### 개발자 A (전담)
- [ ] `src/pipeline/extractor.py` 구현
  - **PDF** → `pymupdf(fitz)` : 페이지별 텍스트 추출, 이미지 전용 페이지 스킵
  - **TXT** → 원문 읽기 (UTF-8 인코딩 처리)
  - **Markdown** → `mistune` 파싱: 섹션 제목(heading) 정보 보존
  - 반환 형식: `list[TextBlock(text, page, section)]`
- [ ] `src/pipeline/chunker.py` 구현
  - 토큰 기준 슬라이딩 윈도우 청킹 (`chunk_size=512`, `overlap=50`)
  - 페이지/섹션 경계에서 강제 분리 처리
  - 반환 형식: `list[ChunkData(index, content, page_number, section_title, char_start, char_end)]`
- [ ] 단위 테스트 작성
  - PDF: 5페이지 샘플 → 청크 수·페이지 번호 검증
  - Markdown: 섹션 제목 보존 검증
  - TXT: 한국어 멀티바이트 청킹 검증

#### 개발자 B (병행)
- → Phase 5 작업 선행 시작 가능

#### 체크포인트
```python
blocks = extract_text("sample_v1.pdf", "pdf")
assert all(b.page is not None for b in blocks)

chunks = chunk_text(blocks, chunk_size=512, overlap=50)
assert all(len(c.content) > 0 for c in chunks)
```

---

### Phase 5 — 임베딩 API 클라이언트 및 업로드 API 구현

**목표**: 외부 임베딩 API 연동 모듈과 문서를 DB에 등록하는 업로드 엔드포인트를 완성한다.

#### 개발자 B (전담)
- [ ] `src/embedding/client.py` 구현
  - OpenAI `text-embedding-3-small` API 호출 (배치당 100개)
  - Retry + Exponential Backoff 내장 (최대 3회)
  - 반환: `list[list[float]]` (벡터 배열)
- [ ] `src/api/routes.py` — 업로드 API 구현 (`POST /api/documents`)
  - 파일 형식 검증 (PDF / TXT / Markdown)
  - 파일 크기 검증 (≤ 100MB)
  - SHA-256 해시 계산 (중복 업로드 감지)
  - staging 파일 저장 후 애플리케이션이 생성한 `version_id` 기준으로 최종 경로에 원자적 이동
  - DB 트랜잭션: `documents` → `document_versions(PENDING)` → `change_log(PENDING)` 동시 INSERT
  - 즉시 `document_id`, `version_id`, `status: PENDING` 반환
- [ ] `src/api/routes.py` — 조회 API 구현
  - `GET /api/documents` — 문서 목록 (카테고리·날짜 필터링)
  - `GET /api/documents/{id}` — 문서 상세 + 버전 이력
  - `GET /api/documents/{id}/status` — 처리 진행 상황

#### 개발자 A (병행)
- → Phase 6 Worker 설계 선행 가능

#### 체크포인트
```bash
# 임베딩 API 연동 확인
python -c "from src.embedding.client import generate_embeddings; import asyncio; v = asyncio.run(generate_embeddings(['테스트'])); print(len(v[0]), '차원')"

# 업로드 API 확인
curl -X POST http://localhost:8000/api/documents \
  -F "file=@sample_v1.pdf" -F "title=보안정책" -F "category=보안"
```

---

### Phase 6 — 비동기 Worker 데몬 구현

**목표**: `change_log`를 지속적으로 감시하고, 파이프라인 단계를 순서대로 실행하는 Worker를 구현한다.

**진입 조건**: Phase 3에서 `TextBlock`/`ChunkData`, 원본 파일 경로(`UPLOAD_DIR/<version_id>.<확장자>`), `change_log` 이벤트·상태 전이 계약을 확정하고, B의 업로드 트랜잭션이 `COMMIT`까지 검증되어야 한다. A는 이 계약 없이 Worker 구현을 고정하지 않는다.

#### 개발자 A (전담)
- [ ] `src/worker/daemon.py` — Worker 루프 구현
  - `SELECT FOR UPDATE SKIP LOCKED` 기반 작업 획득 (동시 처리 충돌 방지)
  - `WORKER_ID` UUID를 `change_log.worker_id`에 기록
  - 폴링 간격: `WORKER_POLL_INTERVAL_SECONDS` (기본값 5초)
- [ ] 상태 전이 로직 구현
  ```
  PENDING      → PROCESSING   (락 획득 시)
  PROCESSING   → COMPLETED    (전체 완료 시)
  PROCESSING   → PENDING      (일시 에러, updated_at/retry_count로 백오프 계산)
  PROCESSING   → DEAD_LETTER  (retry_count >= max_retries, 알림 발송)
  ```
- `document_versions.status`는 `PENDING → PROCESSING → ACTIVE/FAILED`로 별도 관리한다.
- [ ] 타임아웃 된 PROCESSING 항목 복구 로직
  - `locked_at < NOW() - INTERVAL '10 minutes'` 조건으로 PENDING 복귀
- [ ] Backoff 재시도 (30초 → 120초 → 300초) 및 `updated_at`/`retry_count` 기반 선점 지연
- [ ] `systemctl` 등록을 위한 `opensql-worker.service` 유닛 파일 작성

#### 개발자 B (병행)
- → Phase 8 검색 쿼리 설계 선행 가능

#### 체크포인트
```bash
# Worker 단독 실행 테스트
python -m src.worker.daemon

# change_log 상태 확인
psql -c "SELECT id, status, retry_count, event_type FROM doc_search.change_log ORDER BY created_at DESC LIMIT 5;"
```

---

### Phase 7 — Atomic 버전 전환 및 파이프라인 통합

**목표**: Phase 4~6에서 개발한 모듈을 하나의 파이프라인으로 연결하고, 버전 전환이 원자적으로 동작함을 보장한다.

**통합 경계**: B의 임베딩 배치 트랜잭션이 커밋된 뒤 A가 DB 재집계와 `activate_version()`을 수행한다. 두 작업을 서로의 내부 함수에 직접 의존시키지 않고, 배치 저장 결과와 버전 ID를 공유 계약으로 사용한다.

#### 개발자 A
- [ ] `src/pipeline/versioner.py` 구현
  - `activate_version(version_id)` — DB 저장 함수 호출 래퍼
  - 조건 확인: `total_chunks == embedded_chunks` 충족 시만 전환
  - 전환 실패 시 기존 ACTIVE 버전 보존 검증
- [ ] Worker 루프에 파이프라인 단계 순서 통합
  ```
  fetch_job() → extract_text() → chunk_text() → DB INSERT chunks
             → generate_embeddings() → DB INSERT embeddings → activate_version()
  ```
- [ ] 멱등성 검증: Worker 재시작 시 `is_embedded = TRUE` 청크 스킵 동작 확인

#### 개발자 B
- [ ] 임베딩 완료 청크 배치 저장 로직 구현 (배치 트랜잭션)
  ```sql
  BEGIN;
  INSERT INTO doc_search.embeddings (chunk_id, ..., vector) VALUES ...;
  UPDATE doc_search.chunks SET is_embedded = TRUE WHERE id IN (...);
  UPDATE doc_search.document_versions SET embedded_chunks = embedded_chunks + <실제 신규 저장 건수> WHERE id = ...;
  COMMIT;
  ```
- [ ] `processing_status` 뷰를 통한 진행률 API 연동 (`GET /api/documents/{id}/status`)

#### 체크포인트
```sql
-- 버전 전환 후 상태 확인
SELECT version_number, status, embedded_chunks, total_chunks
FROM doc_search.document_versions
WHERE document_id = '<ID>'
ORDER BY version_number;
-- 최신 버전 = ACTIVE, 이전 버전 = ARCHIVED 이어야 함
```

---

### Phase 8 — 벡터 검색 엔진 및 MCP 서버 완성

**목표**: 적재된 임베딩 벡터를 이용한 의미 검색을 구현하고, MCP 프로토콜로 AI 클라이언트에 노출한다.

#### 개발자 B (주도)
- [ ] `src/search/engine.py` — HNSW 기반 코사인 유사도 검색 쿼리 구현
  ```sql
  SELECT c.content, d.title, dv.version_number, c.page_number, c.section_title,
         1 - (e.vector <=> $1::vector) AS similarity
  FROM doc_search.embeddings e
  JOIN doc_search.chunks c       ON e.chunk_id = c.id
  JOIN doc_search.document_versions dv ON e.version_id = dv.id
  JOIN doc_search.documents d    ON dv.document_id = d.id
  WHERE dv.status = 'ACTIVE'
    AND 1 - (e.vector <=> $1::vector) >= $2
  ORDER BY e.vector <=> $1::vector
  LIMIT $3;
  ```
- [ ] 검색 필터 지원: `category`, `tags`, `date_range`, `document_id`, `top_k`
- [ ] `mcp/server.py` — MCP 서버 구현 (4개 Tool 등록)
  - `search_documents(query, top_k, filters)` — 의미 검색
  - `get_document(document_id, version)` — 문서 메타데이터 조회
  - `list_documents(filters, page, page_size)` — 문서 목록
  - `get_chunk(chunk_id)` — 청크 원문 및 위치 조회
- [ ] Claude Desktop 또는 MCP Inspector를 통한 연동 테스트

#### 개발자 A (지원)
- [ ] 검색 쿼리 성능 분석 (`EXPLAIN (ANALYZE, BUFFERS)`)
- [ ] HNSW 파라미터 튜닝 (`ef_search` 조정: 기본값 40)
- [ ] 인덱스 손상 복구 스크립트 작성 (`DROP INDEX` → `CREATE INDEX` 재구축)

#### 체크포인트
```bash
# 현재 스켈레톤의 MCP transport는 stdio이다.
python mcp/server.py
# 실제 도구 호출은 Phase 8 구현 후 MCP Inspector 또는 Claude Desktop으로 검증한다.
# HTTP curl 예시는 별도 HTTP transport adapter를 구현한 경우에만 추가한다.
```

---

### Phase 9 — 통합 테스트 및 데모 시나리오 준비

**목표**: End-to-End 검증을 수행하고, `docs/07_demo_scenario.md` 기반의 시연 환경을 완성한다.

#### 공통 (A & B 함께)

**통합 테스트**
- [ ] **E2E 정상 경로**: `sample_v1.pdf` 업로드 → Worker 처리 → MCP `search_documents` 결과 확인
- [ ] **버전 전환 테스트**: `sample_v2.pdf` 업로드 → 기존 버전 ARCHIVED·신규 버전 ACTIVE 전환 검증
  - 전환 중 검색 요청은 기존 버전 데이터를 반환하는지 확인 (Atomic 보장)
- [ ] **동시성 테스트**: 3개 문서 동시 업로드 → 락 충돌 없이 순차 처리 확인
- [ ] **멱등성 테스트**: Worker 강제 종료 후 재시작 → 중복 임베딩 없이 미완료 청크만 재처리

**장애 복구 시나리오 실연 준비** (`docs/07_demo_scenario.md` 기반)
- [ ] **시나리오 A**: Worker 프로세스 `kill` → 타임아웃 후 자동 PENDING 복귀 확인
- [ ] **시나리오 B**: 임베딩 API 호출 차단 → Backoff 재시도 → DEAD_LETTER 전환 확인
- [ ] **시나리오 C**: OpenSQL Primary 노드 장애 → Patroni Failover → Worker 재시작 후 자동 재처리 확인

**데모 환경 최종 준비**
- [ ] 샘플 파일 준비: `sample_v1.pdf`, `sample_v2.pdf`, `opensql_manual.md`, `faq.txt`
- [ ] 사전 준비 스크립트 작성 (DB 초기화 → 샘플 업로드 → 처리 완료 대기)
- [ ] 발표용 시연 순서지 작성 (시나리오별 예상 소요 시간 포함)

---

## 4. 모듈 간 의존 관계 요약

```
Phase 1  ──► Phase 2  ──► Phase 3
                              │
              ┌───────────────┴──────────────┐
              ▼                              ▼
          Phase 4 (A)                   Phase 5 (B)
          extractor, chunker            embedding client, upload API
              │                              │
              └───────────────┬──────────────┘
                              ▼
                     Phase 6 (A) — Worker 데몬
                              │
                     Phase 7 (A+B) — 파이프라인 통합
                              │
                     Phase 8 (B+A) — 검색 엔진 + MCP 서버
                              │
                     Phase 9 (A+B) — 통합 테스트 + 데모 준비
```

---

## 5. 협업 Ground Rules

1. **스키마 변경 시**: DDL 변경은 반드시 양측 합의 후 `sql/` 디렉터리에 마이그레이션 스크립트로 남긴다.
2. **인터페이스 우선**: Phase 3에서 합의한 함수 시그니처 변경 시 상대방에게 즉시 공유한다.
3. **멱등성 원칙**: 모든 파이프라인 단계는 재실행해도 동일한 결과가 나와야 한다.
4. **에러 반드시 로깅**: Worker 에러는 `change_log.error_message`에 기록하고 알림을 발송한다.
5. **Phase 체크포인트 공유**: 각 Phase 완료 시 체크포인트 명령어를 함께 실행하여 상태를 공유한다.
