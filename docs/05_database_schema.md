# 데이터베이스 스키마 설계 (Database Schema Design)

**프로젝트명**: OpenSQL 기반 AI 문서 검색 및 버전 관리 시스템  
**문서 버전**: v1.0  
**작성일**: 2026-08-05  
**대상 DB**: Tmax OpenSQL 3.17.8.7 (PostgreSQL 17.8 + pgvector 0.8.1)

---

## 1. 스키마 개요

```
Schema: doc_search
├── documents          (문서 원본 정보)
├── document_versions  (버전별 메타데이터 및 처리 설정)
├── chunks             (청크 텍스트 및 위치 정보)
├── embeddings         (pgvector 임베딩 벡터)
├── change_log         (변경 이벤트 및 처리 상태)
└── embedding_models   (사용된 임베딩 모델 레지스트리)
```

---

## 2. 스키마 생성 SQL

### 2.1 Extension 및 Schema 초기화

```sql
-- pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 전용 스키마 생성
CREATE SCHEMA IF NOT EXISTS doc_search;
SET search_path TO doc_search, public;
```

---

### 2.2 embedding_models (임베딩 모델 레지스트리)

```sql
CREATE TABLE doc_search.embedding_models (
    id              SERIAL PRIMARY KEY,
    model_name      VARCHAR(100)  NOT NULL,          -- 예: 'text-embedding-3-small'
    model_version   VARCHAR(50)   NOT NULL,          -- 예: '2024-01-01'
    provider        VARCHAR(50)   NOT NULL,          -- 예: 'openai', 'cohere'
    dimensions      INTEGER       NOT NULL,          -- 임베딩 차원수 (예: 1536)
    max_tokens      INTEGER,                         -- 모델 최대 입력 토큰 수
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    notes           TEXT,

    CONSTRAINT uq_model_name_version UNIQUE (model_name, model_version)
);

COMMENT ON TABLE doc_search.embedding_models IS '사용된 임베딩 모델 레지스트리. 모델 교체 이력 추적에 사용';
```

---

### 2.3 documents (문서 원본 정보)

```sql
CREATE TABLE doc_search.documents (
    id              UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           VARCHAR(500)  NOT NULL,          -- 문서 제목
    file_type       VARCHAR(20)   NOT NULL,          -- 'pdf', 'txt', 'markdown'
    file_size_bytes BIGINT,                          -- 원본 파일 크기 (bytes)
    category        VARCHAR(100),                    -- 분류 카테고리
    tags            TEXT[],                          -- 태그 배열
    uploader_id     VARCHAR(200),                    -- 업로더 식별자 (선택)
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT chk_file_type CHECK (file_type IN ('pdf', 'txt', 'markdown'))
);

CREATE INDEX idx_documents_category    ON doc_search.documents (category) WHERE NOT is_deleted;
CREATE INDEX idx_documents_tags        ON doc_search.documents USING GIN (tags) WHERE NOT is_deleted;
CREATE INDEX idx_documents_created_at  ON doc_search.documents (created_at) WHERE NOT is_deleted;

COMMENT ON TABLE doc_search.documents IS '업로드된 문서의 원본 정보. 버전과 무관한 고정 메타데이터';
COMMENT ON COLUMN doc_search.documents.id IS 'UUID 기반 문서 고유 식별자';
```

---

### 2.4 document_versions (버전별 메타데이터)

```sql
-- 버전 상태 ENUM
CREATE TYPE doc_search.version_status AS ENUM (
    'PENDING',      -- 업로드 완료, 처리 대기 중
    'PROCESSING',   -- 파이프라인 처리 진행 중
    'ACTIVE',       -- 현재 검색 대상 활성 버전
    'ARCHIVED',     -- 이전 버전 (보존)
    'FAILED'        -- 처리 실패
);

CREATE TABLE doc_search.document_versions (
    id                  UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id         UUID          NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,
    version_number      INTEGER       NOT NULL,          -- 1부터 시작하는 순번
    status              doc_search.version_status NOT NULL DEFAULT 'PENDING',

    -- 처리 설정 (재현성을 위해 저장)
    embedding_model_id  INTEGER       REFERENCES doc_search.embedding_models(id),
    chunk_size          INTEGER       NOT NULL DEFAULT 512,
    chunk_overlap       INTEGER       NOT NULL DEFAULT 50,

    -- 처리 통계
    total_chunks        INTEGER       DEFAULT 0,
    embedded_chunks     INTEGER       DEFAULT 0,         -- 임베딩 완료된 청크 수

    -- 타임스탬프
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    processing_started_at TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    activated_at        TIMESTAMPTZ,                     -- ACTIVE 전환 시각

    -- 처리 메타데이터
    error_message       TEXT,                            -- 실패 시 오류 내용
    file_hash           VARCHAR(64),                     -- SHA-256 (중복 업로드 감지용)

    CONSTRAINT uq_document_version UNIQUE (document_id, version_number),
    CONSTRAINT chk_chunk_size      CHECK (chunk_size > 0),
    CONSTRAINT chk_chunk_overlap   CHECK (chunk_overlap >= 0 AND chunk_overlap < chunk_size)
);

-- 한 문서에 ACTIVE 버전은 최대 1개
CREATE UNIQUE INDEX uq_one_active_version
    ON doc_search.document_versions (document_id)
    WHERE status = 'ACTIVE';

CREATE INDEX idx_doc_versions_document_id ON doc_search.document_versions (document_id);
CREATE INDEX idx_doc_versions_status      ON doc_search.document_versions (status);

COMMENT ON TABLE doc_search.document_versions IS '문서 버전별 처리 상태 및 설정. ACTIVE 버전만 검색 대상';
COMMENT ON COLUMN doc_search.document_versions.file_hash IS 'SHA-256 해시. 동일 파일 재업로드 감지에 활용';
```

---

### 2.5 chunks (청크 텍스트 및 위치 정보)

```sql
CREATE TABLE doc_search.chunks (
    id              UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_id      UUID          NOT NULL REFERENCES doc_search.document_versions(id) ON DELETE CASCADE,
    document_id     UUID          NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,

    -- 청크 내용 및 위치
    chunk_index     INTEGER       NOT NULL,             -- 버전 내 청크 순번 (0부터)
    content         TEXT          NOT NULL,             -- 청크 텍스트
    page_number     INTEGER,                            -- PDF 페이지 번호 (해당하는 경우)
    section_title   VARCHAR(500),                       -- Markdown 섹션 제목 (해당하는 경우)
    char_start      INTEGER,                            -- 원문 내 시작 문자 위치
    char_end        INTEGER,                            -- 원문 내 종료 문자 위치

    -- 처리 상태
    is_embedded     BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    embedded_at     TIMESTAMPTZ,                        -- 임베딩 완료 시각

    CONSTRAINT uq_version_chunk_index UNIQUE (version_id, chunk_index)
);

CREATE INDEX idx_chunks_version_id    ON doc_search.chunks (version_id);
CREATE INDEX idx_chunks_document_id   ON doc_search.chunks (document_id);
CREATE INDEX idx_chunks_page_number   ON doc_search.chunks (version_id, page_number);
CREATE INDEX idx_chunks_not_embedded  ON doc_search.chunks (version_id) WHERE NOT is_embedded;

COMMENT ON TABLE doc_search.chunks IS '청크 텍스트 및 원본 위치 정보. 임베딩과 1:1 대응';
```

---

### 2.6 embeddings (pgvector 임베딩 벡터)

```sql
CREATE TABLE doc_search.embeddings (
    id                  UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    chunk_id            UUID          NOT NULL UNIQUE REFERENCES doc_search.chunks(id) ON DELETE CASCADE,
    version_id          UUID          NOT NULL REFERENCES doc_search.document_versions(id) ON DELETE CASCADE,
    document_id         UUID          NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,
    embedding_model_id  INTEGER       NOT NULL REFERENCES doc_search.embedding_models(id),

    -- pgvector 벡터 컬럼 (차원수는 모델에 맞게 설정, 기본 1536)
    vector              vector(1536)  NOT NULL,

    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- HNSW 인덱스 (코사인 유사도 기준 근사 최근접 이웃 검색)
-- 파라미터: m=16 (레이어당 연결 수), ef_construction=64 (인덱스 구축 품질)
CREATE INDEX idx_embeddings_hnsw
    ON doc_search.embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 버전 기반 검색 필터링용 인덱스
CREATE INDEX idx_embeddings_version_id   ON doc_search.embeddings (version_id);
CREATE INDEX idx_embeddings_document_id  ON doc_search.embeddings (document_id);

COMMENT ON TABLE doc_search.embeddings IS 'pgvector 임베딩 벡터 저장. HNSW 인덱스로 ANN 검색 지원';
COMMENT ON COLUMN doc_search.embeddings.vector IS '1536차원 벡터 (OpenAI text-embedding-3-small 기준). 모델 변경 시 차원 수정 필요';
```

---

### 2.7 change_log (변경 이벤트 및 처리 상태)

```sql
-- 이벤트 유형 ENUM
CREATE TYPE doc_search.change_event_type AS ENUM (
    'UPLOAD',           -- 새 문서 업로드
    'UPDATE',           -- 기존 문서 새 버전 등록
    'DELETE',           -- 문서 삭제
    'EMBED_START',      -- 임베딩 처리 시작
    'EMBED_COMPLETE',   -- 임베딩 처리 완료
    'EMBED_FAIL',       -- 임베딩 처리 실패
    'VERSION_SWITCH'    -- ACTIVE 버전 전환
);

-- 작업 상태 ENUM
CREATE TYPE doc_search.job_status AS ENUM (
    'PENDING',          -- 처리 대기
    'PROCESSING',       -- 처리 중 (Worker가 락 보유)
    'COMPLETED',        -- 처리 완료
    'FAILED',           -- 처리 실패
    'DEAD_LETTER'       -- 최대 재시도 초과
);

CREATE TABLE doc_search.change_log (
    id                  BIGSERIAL     PRIMARY KEY,
    event_type          doc_search.change_event_type NOT NULL,
    status              doc_search.job_status        NOT NULL DEFAULT 'PENDING',

    -- 대상 엔티티
    document_id         UUID          NOT NULL,
    version_id          UUID,                              -- 버전 연관 이벤트의 경우
    chunk_id            UUID,                              -- 청크 연관 이벤트의 경우

    -- 처리 이력
    retry_count         INTEGER       NOT NULL DEFAULT 0,
    max_retries         INTEGER       NOT NULL DEFAULT 3,
    worker_id           VARCHAR(200),                      -- 처리 중인 Worker 식별자
    locked_at           TIMESTAMPTZ,                       -- Worker 락 획득 시각

    -- 타임스탬프
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,

    -- 오류 정보
    error_message       TEXT,
    error_detail        JSONB                              -- 스택 트레이스 등 상세 정보
);

-- Worker가 처리할 PENDING 항목 조회용 (SELECT FOR UPDATE SKIP LOCKED 패턴)
CREATE INDEX idx_change_log_pending
    ON doc_search.change_log (created_at ASC)
    WHERE status = 'PENDING';

CREATE INDEX idx_change_log_document_id   ON doc_search.change_log (document_id);
CREATE INDEX idx_change_log_version_id    ON doc_search.change_log (version_id) WHERE version_id IS NOT NULL;

COMMENT ON TABLE doc_search.change_log IS '모든 변경 이벤트 기록 및 처리 상태. 장애 복구 및 재시도의 근거';
COMMENT ON COLUMN doc_search.change_log.worker_id IS 'PROCESSING 상태일 때 처리 중인 Worker 인스턴스 ID';
```

---

## 3. 핵심 뷰 (Views)

### 3.1 active_document_chunks (검색 대상 청크 조회용)

```sql
-- 현재 검색 가능한 청크와 문서 정보를 조인한 뷰
CREATE VIEW doc_search.active_document_chunks AS
SELECT
    c.id            AS chunk_id,
    c.content       AS chunk_text,
    c.chunk_index,
    c.page_number,
    c.section_title,
    dv.id           AS version_id,
    dv.version_number,
    d.id            AS document_id,
    d.title         AS document_title,
    d.category,
    d.tags,
    e.vector,
    e.id            AS embedding_id
FROM doc_search.chunks c
JOIN doc_search.document_versions dv ON c.version_id = dv.id
JOIN doc_search.documents d          ON c.document_id = d.id
JOIN doc_search.embeddings e         ON e.chunk_id = c.id
WHERE dv.status = 'ACTIVE'
  AND d.is_deleted = FALSE;

COMMENT ON VIEW doc_search.active_document_chunks IS 'ACTIVE 버전의 모든 청크 + 벡터 조인 뷰. 검색 쿼리의 기반';
```

### 3.2 processing_status (처리 현황 모니터링용)

```sql
CREATE VIEW doc_search.processing_status AS
SELECT
    d.title                         AS document_title,
    dv.version_number,
    dv.status                       AS version_status,
    dv.total_chunks,
    dv.embedded_chunks,
    ROUND(
        CASE WHEN dv.total_chunks > 0
             THEN 100.0 * dv.embedded_chunks / dv.total_chunks
             ELSE 0 END, 1
    )                               AS embedding_progress_pct,
    dv.created_at                   AS version_created_at,
    dv.processing_completed_at,
    cl.retry_count,
    cl.status                       AS job_status,
    cl.error_message
FROM doc_search.document_versions dv
JOIN doc_search.documents d  ON dv.document_id = d.id
LEFT JOIN doc_search.change_log cl ON cl.version_id = dv.id
    AND cl.status NOT IN ('COMPLETED')
ORDER BY dv.created_at DESC;
```

---

## 4. 핵심 함수 (Functions)

### 4.1 버전 Atomic 전환 함수

```sql
CREATE OR REPLACE FUNCTION doc_search.activate_version(
    p_version_id UUID
) RETURNS VOID AS $$
DECLARE
    v_document_id UUID;
BEGIN
    -- 대상 버전의 document_id 조회
    SELECT document_id INTO v_document_id
    FROM doc_search.document_versions
    WHERE id = p_version_id AND status = 'PROCESSING';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Version % not found or not in PROCESSING status', p_version_id;
    END IF;

    -- 단일 트랜잭션으로 기존 ACTIVE → ARCHIVED, 새 버전 → ACTIVE
    UPDATE doc_search.document_versions
    SET status = 'ARCHIVED',
        updated_at = NOW()
    WHERE document_id = v_document_id
      AND status = 'ACTIVE';

    UPDATE doc_search.document_versions
    SET status = 'ACTIVE',
        activated_at = NOW(),
        processing_completed_at = NOW()
    WHERE id = p_version_id;

    -- 변경 로그 기록
    INSERT INTO doc_search.change_log (event_type, status, document_id, version_id)
    VALUES ('VERSION_SWITCH', 'COMPLETED', v_document_id, p_version_id);
END;
$$ LANGUAGE plpgsql;
```

### 4.2 의미 검색 함수

```sql
CREATE OR REPLACE FUNCTION doc_search.search_documents(
    p_query_vector  vector(1536),
    p_top_k         INTEGER DEFAULT 5,
    p_category      VARCHAR DEFAULT NULL,
    p_min_score     FLOAT DEFAULT 0.0
) RETURNS TABLE (
    chunk_id        UUID,
    chunk_text      TEXT,
    document_id     UUID,
    document_title  VARCHAR,
    version_number  INTEGER,
    page_number     INTEGER,
    section_title   VARCHAR,
    similarity      FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        adc.chunk_id,
        adc.chunk_text,
        adc.document_id,
        adc.document_title,
        adc.version_number,
        adc.page_number,
        adc.section_title,
        (1 - (adc.vector <=> p_query_vector))::FLOAT AS similarity
    FROM doc_search.active_document_chunks adc
    WHERE
        (p_category IS NULL OR adc.category = p_category)
        AND (1 - (adc.vector <=> p_query_vector)) >= p_min_score
    ORDER BY adc.vector <=> p_query_vector  -- 코사인 거리 오름차순 = 유사도 내림차순
    LIMIT p_top_k;
END;
$$ LANGUAGE plpgsql STABLE;
```

---

## 5. 테이블 간 관계도 (ERD)

```
embedding_models
    │ (1)
    │ has many
    ▼ (N)
document_versions ──────── documents
    │ (1)                      │ (1)
    │ has many                 │ has many
    ▼ (N)                      ▼ (N)
  chunks ──────── document_versions (via version_id)
    │ (1)
    │ has one
    ▼ (1)
embeddings

change_log ──── documents (via document_id)
           └─── document_versions (via version_id)
           └─── chunks (via chunk_id)
```

---

## 6. 인덱스 전략 요약

| 테이블 | 인덱스 | 타입 | 목적 |
|--------|--------|------|------|
| embeddings | idx_embeddings_hnsw | HNSW | 코사인 유사도 ANN 검색 |
| documents | idx_documents_tags | GIN | 태그 배열 검색 |
| document_versions | uq_one_active_version | UNIQUE PARTIAL | ACTIVE 버전 유일성 보장 |
| change_log | idx_change_log_pending | PARTIAL | Worker PENDING 항목 빠른 조회 |
| chunks | idx_chunks_not_embedded | PARTIAL | 미임베딩 청크 빠른 조회 |

---

## 7. pgvector HNSW vs IVFFlat 선택 기준

| 항목 | HNSW | IVFFlat |
|------|------|---------|
| 검색 속도 | 빠름 | 보통 |
| 인덱스 구축 시간 | 느림 | 빠름 |
| 메모리 사용량 | 높음 | 낮음 |
| 정확도 | 높음 | 보통 |
| **권장 상황** | **운영 환경 (본 프로젝트)** | 초기 프로토타입 |

**본 프로젝트에서는 HNSW(m=16, ef_construction=64)를 기본으로 사용하며,  
검색 시 `SET hnsw.ef_search = 40;` (기본값)으로 품질을 조절할 수 있다.**
