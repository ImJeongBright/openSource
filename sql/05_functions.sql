-- Stored functions for a clean Qwen3 Embedding (1024d) installation.

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

    -- A retry may reach activation after the previous transaction already
    -- activated this version. Treat that case as an idempotent success.
    SELECT status INTO v_status
    FROM doc_search.document_versions
    WHERE id = p_version_id
    FOR UPDATE;

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

CREATE OR REPLACE FUNCTION doc_search.search_documents(
    p_query_vector  vector(1024),
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
    IF p_top_k < 1 OR p_top_k > 100 THEN
        RAISE EXCEPTION 'p_top_k must be between 1 and 100';
    END IF;

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
    WHERE (p_category IS NULL OR adc.category = p_category)
      AND (1 - (adc.vector <=> p_query_vector)) >= p_min_score
    ORDER BY adc.vector <=> p_query_vector
    LIMIT p_top_k;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION doc_search.activate_version(UUID) IS
    'Atomically archives the prior ACTIVE version and activates a PROCESSING version.';
COMMENT ON FUNCTION doc_search.search_documents(vector, INTEGER, VARCHAR, FLOAT) IS
    'Cosine ANN search for 1024-dimensional Qwen3 embeddings.';
