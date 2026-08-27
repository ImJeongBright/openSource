-- ============================================================
-- sql/03_core_tables.sql
-- 코어 엔티티 테이블 3종: documents, document_versions, chunks
-- 담당: 개발자 A
-- ============================================================

-- ------------------------------------------------------------
-- documents: 문서 원본 정보 (버전과 무관한 고정 메타데이터)
-- ------------------------------------------------------------
CREATE TABLE doc_search.documents (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    title           VARCHAR(500)  NOT NULL,
    file_type       VARCHAR(20)   NOT NULL,          -- 'pdf', 'txt', 'markdown'
    file_size_bytes BIGINT,
    category        VARCHAR(100),
    tags            TEXT[],
    uploader_id     VARCHAR(200),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted      BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT chk_file_type CHECK (file_type IN ('pdf', 'txt', 'markdown'))
);

COMMENT ON TABLE  doc_search.documents IS '업로드된 문서의 원본 정보. 버전과 무관한 고정 메타데이터';
COMMENT ON COLUMN doc_search.documents.id IS 'UUID 기반 문서 고유 식별자';

-- ------------------------------------------------------------
-- document_versions: 버전별 처리 상태 및 설정
-- ------------------------------------------------------------
CREATE TABLE doc_search.document_versions (
    id                      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             UUID          NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,
    version_number          INTEGER       NOT NULL,
    status                  doc_search.version_status NOT NULL DEFAULT 'PENDING',

    -- 처리 설정 (재현성을 위해 버전별로 저장)
    embedding_model_id      INTEGER,      -- FK는 06_app_tables.sql 적용 후 추가됨
    chunk_size              INTEGER       NOT NULL DEFAULT 512,
    chunk_overlap           INTEGER       NOT NULL DEFAULT 50,

    -- 처리 통계
    total_chunks            INTEGER       DEFAULT 0,
    embedded_chunks         INTEGER       DEFAULT 0,

    -- 타임스탬프
    created_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    processing_started_at   TIMESTAMPTZ,
    processing_completed_at TIMESTAMPTZ,
    activated_at            TIMESTAMPTZ,

    -- 처리 메타데이터
    error_message           TEXT,
    file_hash               VARCHAR(64),  -- SHA-256 (중복 업로드 감지용)

    CONSTRAINT uq_document_version UNIQUE (document_id, version_number),
    CONSTRAINT chk_chunk_size      CHECK (chunk_size > 0),
    CONSTRAINT chk_chunk_overlap   CHECK (chunk_overlap >= 0 AND chunk_overlap < chunk_size)
);

-- 한 문서에 ACTIVE 버전은 최대 1개 (부분 유니크 인덱스)
CREATE UNIQUE INDEX uq_one_active_version
    ON doc_search.document_versions (document_id)
    WHERE status = 'ACTIVE';

COMMENT ON TABLE  doc_search.document_versions IS '문서 버전별 처리 상태 및 설정. ACTIVE 버전만 검색 대상';
COMMENT ON COLUMN doc_search.document_versions.file_hash IS 'SHA-256 해시. 동일 파일 재업로드 감지에 활용';

-- ------------------------------------------------------------
-- chunks: 청크 텍스트 및 원본 위치 정보
-- ------------------------------------------------------------
CREATE TABLE doc_search.chunks (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id      UUID          NOT NULL REFERENCES doc_search.document_versions(id) ON DELETE CASCADE,
    document_id     UUID          NOT NULL REFERENCES doc_search.documents(id) ON DELETE CASCADE,

    chunk_index     INTEGER       NOT NULL,
    content         TEXT          NOT NULL,
    page_number     INTEGER,
    section_title   VARCHAR(500),
    char_start      INTEGER,
    char_end        INTEGER,

    is_embedded     BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    embedded_at     TIMESTAMPTZ,

    CONSTRAINT uq_version_chunk_index UNIQUE (version_id, chunk_index)
);

COMMENT ON TABLE doc_search.chunks IS '청크 텍스트 및 원본 위치 정보. 임베딩과 1:1 대응';
