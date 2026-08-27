CREATE OR REPLACE VIEW doc_search.active_document_chunks AS
SELECT
    c.id AS chunk_id,
    c.content AS chunk_text,
    c.chunk_index,
    c.page_number,
    c.section_title,
    dv.id AS version_id,
    dv.version_number,
    d.id AS document_id,
    d.title AS document_title,
    d.category,
    d.tags,
    e.vector,
    e.id AS embedding_id
FROM doc_search.chunks c
JOIN doc_search.document_versions dv ON c.version_id = dv.id
JOIN doc_search.documents d ON c.document_id = d.id
JOIN doc_search.embeddings e ON e.chunk_id = c.id
WHERE dv.status = 'ACTIVE'
  AND d.is_deleted = FALSE;

CREATE OR REPLACE VIEW doc_search.processing_status AS
SELECT
    d.id AS document_id,
    d.title AS document_title,
    dv.id AS version_id,
    dv.version_number,
    dv.status AS version_status,
    dv.total_chunks,
    dv.embedded_chunks,
    ROUND(
        CASE
            WHEN dv.total_chunks > 0
            THEN 100.0 * dv.embedded_chunks / dv.total_chunks
            ELSE 0
        END,
        1
    ) AS embedding_progress_pct,
    dv.created_at AS version_created_at,
    dv.processing_started_at,
    dv.processing_completed_at,
    cl.retry_count,
    cl.status AS job_status,
    cl.error_message,
    cl.worker_id
FROM doc_search.document_versions dv
JOIN doc_search.documents d ON dv.document_id = d.id
LEFT JOIN LATERAL (
    SELECT latest.retry_count, latest.status, latest.error_message, latest.worker_id
    FROM doc_search.change_log latest
    WHERE latest.version_id = dv.id
    ORDER BY latest.created_at DESC, latest.id DESC
    LIMIT 1
) cl ON TRUE;

COMMENT ON VIEW doc_search.active_document_chunks IS
    'Traceable chunks and vectors from ACTIVE, non-deleted documents.';
COMMENT ON VIEW doc_search.processing_status IS
    'One processing progress row per version with its latest job, including completion.';
