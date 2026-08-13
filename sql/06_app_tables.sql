-- ============================================================
-- sql/06_app_tables.sql
-- 적재·운영 테이블 3종: embedding_models, embeddings, change_log
-- 담당: 개발자 B
-- ============================================================

-- ------------------------------------------------------------
-- embedding_models: 임베딩 모델 레지스트리
-- ------------------------------------------------------------
CREATE TABLE doc_search.embedding_models (
    id            SERIAL        PRIMARY KEY,
    model_name    VARCHAR(100)  NOT NULL,
    model_version VARCHAR(50)   NOT NULL,
    provider      VARCHAR(50)   NOT NULL,  -- 'openai', 'cohere', etc.
    dimensions    INTEGER       NOT NULL,
    max_tokens    INTEGER,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    notes         TEXT,

    CONSTRAINT uq_model_name_version UNIQUE (model_name, model_version)
);

COMMENT ON TABLE doc_search.embedding_models IS '사용된 임베딩 모델 레지스트리. 모델 교체 이력 추적에 사용';

-- document_versions 테이블의 embedding_model_id FK 추가
-- (03_core_tables.sql 적용 시점에는 embedding_models 미존재이므로 여기서 추가)
ALTER TABLE doc_search.document_versions
    ADD CONSTRAINT fk_doc_versions_embedding_model
    FOREIGN KEY (embedding_model_id) REFERENCES doc_search.embedding_models(id);

-- ------------------------------------------------------------
-- embeddings: pgvector 임베딩 벡터 저장
-- ------------------------------------------------------------
CREATE TABLE doc_search.embeddings (
    id                 UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    chunk_id           UUID         NOT NULL UNIQUE REFERENCES doc_search.chunks(id) ON DELETE CASCADE,
    version_id         UUID         NOT NULL REFERENCES doc_search.document_versions(id) ON DELETE CASCADE,
    document_id        UUID         NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,
    embedding_model_id INTEGER      NOT NULL REFERENCES doc_search.embedding_models(id),

    -- pgvector 벡터 컬럼 (기본 1536차원, 모델 변경 시 수정 필요)
    vector             vector(1536) NOT NULL,

    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  doc_search.embeddings IS 'pgvector 임베딩 벡터 저장. HNSW 인덱스로 ANN 검색 지원';
COMMENT ON COLUMN doc_search.embeddings.vector IS '1536차원 벡터 (OpenAI text-embedding-3-small 기준). 모델 변경 시 차원 수정 필요';

-- ------------------------------------------------------------
-- change_log: 변경 이벤트 및 Worker 처리 상태
-- ------------------------------------------------------------
CREATE TABLE doc_search.change_log (
    id            BIGSERIAL     PRIMARY KEY,
    event_type    doc_search.change_event_type NOT NULL,
    status        doc_search.job_status        NOT NULL DEFAULT 'PENDING',

    -- 대상 엔티티
    document_id   UUID          NOT NULL,
    version_id    UUID,
    chunk_id      UUID,

    -- Worker 처리 이력
    retry_count   INTEGER       NOT NULL DEFAULT 0,
    max_retries   INTEGER       NOT NULL DEFAULT 3,
    worker_id     VARCHAR(200),   -- 처리 중인 Worker 인스턴스 식별자
    locked_at     TIMESTAMPTZ,    -- Worker 락 획득 시각

    -- 타임스탬프
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,

    -- 오류 정보
    error_message TEXT,
    error_detail  JSONB           -- 스택 트레이스 등 상세 정보
);

COMMENT ON TABLE  doc_search.change_log IS '모든 변경 이벤트 기록 및 Worker 처리 상태. 장애 복구 및 재시도의 근거';
COMMENT ON COLUMN doc_search.change_log.worker_id IS 'PROCESSING 상태일 때 처리 중인 Worker 인스턴스 ID';
