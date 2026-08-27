-- Preserve the latest terminal job so ACTIVE versions report COMPLETED instead of NULL.

BEGIN;

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

COMMENT ON VIEW doc_search.processing_status IS
    'One processing progress row per version with its latest job, including completion.';

COMMIT;
