-- ============================================================
-- sql/07_views.sql
-- 운영 뷰 2종: active_document_chunks, processing_status
-- 담당: 개발자 B
-- ============================================================

-- ------------------------------------------------------------
-- active_document_chunks: 검색 대상 청크 + 벡터 조인 뷰
-- search_documents() 함수 및 검색 엔진의 기반이 됨
-- ------------------------------------------------------------
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

COMMENT ON VIEW doc_search.active_document_chunks IS
    'ACTIVE 버전의 모든 청크 + 임베딩 벡터 조인 뷰. 검색 쿼리의 기반';

-- ------------------------------------------------------------
-- processing_status: 문서별 처리 진행률 모니터링 뷰
-- GET /api/documents/{id}/status API에서 사용
-- ------------------------------------------------------------
CREATE VIEW doc_search.processing_status AS
SELECT
    d.id                            AS document_id,
    d.title                         AS document_title,
    dv.id                           AS version_id,
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
    dv.processing_started_at,
    dv.processing_completed_at,
    cl.retry_count,
    cl.status                       AS job_status,
    cl.error_message,
    cl.worker_id
FROM doc_search.document_versions dv
JOIN doc_search.documents d  ON dv.document_id = d.id
LEFT JOIN doc_search.change_log cl ON cl.version_id = dv.id
    AND cl.status NOT IN ('COMPLETED')
ORDER BY dv.created_at DESC;

COMMENT ON VIEW doc_search.processing_status IS
    '문서별 임베딩 처리 진행률 및 Worker 상태 모니터링 뷰';
