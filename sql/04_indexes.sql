-- ============================================================
-- sql/04_indexes.sql
-- B-Tree 인덱스 및 HNSW 벡터 인덱스 생성
-- 담당: 개발자 A
-- ※ 03_core_tables.sql 및 06_app_tables.sql 적용 후 실행
-- ============================================================

-- ------------------------------------------------------------
-- documents 인덱스
-- ------------------------------------------------------------
CREATE INDEX idx_documents_category   ON doc_search.documents (category) WHERE NOT is_deleted;
CREATE INDEX idx_documents_tags       ON doc_search.documents USING GIN (tags) WHERE NOT is_deleted;
CREATE INDEX idx_documents_created_at ON doc_search.documents (created_at) WHERE NOT is_deleted;

-- ------------------------------------------------------------
-- document_versions 인덱스
-- ------------------------------------------------------------
CREATE INDEX idx_doc_versions_document_id ON doc_search.document_versions (document_id);
CREATE INDEX idx_doc_versions_status      ON doc_search.document_versions (status);

-- ------------------------------------------------------------
-- chunks 인덱스
-- ------------------------------------------------------------
CREATE INDEX idx_chunks_version_id   ON doc_search.chunks (version_id);
CREATE INDEX idx_chunks_document_id  ON doc_search.chunks (document_id);
CREATE INDEX idx_chunks_page_number  ON doc_search.chunks (version_id, page_number);

-- 미임베딩 청크 빠른 조회 (Worker에서 사용)
CREATE INDEX idx_chunks_not_embedded ON doc_search.chunks (version_id) WHERE NOT is_embedded;

-- ------------------------------------------------------------
-- embeddings 인덱스
-- ※ 06_app_tables.sql의 embeddings 테이블 생성 후 실행됨
-- ------------------------------------------------------------

-- HNSW 인덱스 (코사인 유사도 기준 ANN 검색)
-- m=16: 레이어당 연결 수 (높을수록 정확, 메모리 증가)
-- ef_construction=64: 인덱스 구축 품질 (높을수록 정확, 구축 시간 증가)
CREATE INDEX idx_embeddings_hnsw
    ON doc_search.embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_embeddings_version_id  ON doc_search.embeddings (version_id);
CREATE INDEX idx_embeddings_document_id ON doc_search.embeddings (document_id);

-- ------------------------------------------------------------
-- change_log 인덱스
-- ※ 06_app_tables.sql의 change_log 테이블 생성 후 실행됨
-- ------------------------------------------------------------

-- Worker가 처리할 PENDING 항목 조회용 (SELECT FOR UPDATE SKIP LOCKED)
CREATE INDEX idx_change_log_pending
    ON doc_search.change_log (created_at ASC)
    WHERE status = 'PENDING';

CREATE INDEX idx_change_log_document_id ON doc_search.change_log (document_id);
CREATE INDEX idx_change_log_version_id  ON doc_search.change_log (version_id) WHERE version_id IS NOT NULL;
