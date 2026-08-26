BEGIN;

CREATE OR REPLACE FUNCTION doc_search.activate_version(
    p_version_id UUID
) RETURNS VOID AS $$
DECLARE
    v_document_id UUID;
    v_status      doc_search.version_status;
BEGIN
    SELECT document_id, status INTO v_document_id, v_status
    FROM doc_search.document_versions
    WHERE id = p_version_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Version % not found', p_version_id;
    END IF;

    PERFORM 1
    FROM doc_search.documents
    WHERE id = v_document_id
    FOR UPDATE;

    SELECT status INTO v_status
    FROM doc_search.document_versions
    WHERE id = p_version_id
    FOR UPDATE;

    -- A retry after a successful activation is an idempotent success.
    IF v_status = 'ACTIVE' THEN
        RETURN;
    END IF;

    IF v_status <> 'PROCESSING' THEN
        RAISE EXCEPTION 'Version % is not in PROCESSING status', p_version_id;
    END IF;

    UPDATE doc_search.document_versions
    SET status = 'ARCHIVED', updated_at = NOW()
    WHERE document_id = v_document_id AND status = 'ACTIVE';

    UPDATE doc_search.document_versions
    SET status = 'ACTIVE',
        updated_at = NOW(),
        activated_at = NOW(),
        processing_completed_at = NOW()
    WHERE id = p_version_id;

    INSERT INTO doc_search.change_log (event_type, status, document_id, version_id)
    VALUES ('VERSION_SWITCH', 'COMPLETED', v_document_id, p_version_id);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION doc_search.activate_version(UUID) IS
    'Atomically archives the prior ACTIVE version and idempotently activates a PROCESSING version.';

COMMIT;
