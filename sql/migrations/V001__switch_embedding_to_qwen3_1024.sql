-- Switch an existing OpenAI/1536d schema to local Qwen3/1024d embeddings.
-- Safety policy: existing vectors are never truncated or converted. Re-embed
-- them explicitly before running this migration, or clear them in a separately
-- reviewed data migration.

BEGIN;

SELECT pg_advisory_xact_lock(hashtextextended('qwen3-embedding-1024-migration', 0));
LOCK TABLE doc_search.embeddings IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    current_vector_type TEXT;
    embedding_count BIGINT;
BEGIN
    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    INTO current_vector_type
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'doc_search.embeddings'::regclass
      AND attribute.attname = 'vector'
      AND NOT attribute.attisdropped;

    IF current_vector_type IS NULL THEN
        RAISE EXCEPTION 'doc_search.embeddings.vector does not exist';
    END IF;

    SELECT COUNT(*) INTO embedding_count FROM doc_search.embeddings;
    IF current_vector_type <> 'vector(1024)' AND embedding_count > 0 THEN
        RAISE EXCEPTION
            'Migration stopped: % existing % vectors require explicit re-embedding',
            embedding_count,
            current_vector_type;
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS doc_search.search_documents(
    vector(1536), INTEGER, VARCHAR, FLOAT
);
DROP FUNCTION IF EXISTS doc_search.search_documents(
    vector(1024), INTEGER, VARCHAR, FLOAT
);
DROP VIEW IF EXISTS doc_search.active_document_chunks;
DROP INDEX IF EXISTS doc_search.idx_embeddings_hnsw;

DO $$
DECLARE
    current_vector_type TEXT;
BEGIN
    SELECT format_type(attribute.atttypid, attribute.atttypmod)
    INTO current_vector_type
    FROM pg_attribute attribute
    WHERE attribute.attrelid = 'doc_search.embeddings'::regclass
      AND attribute.attname = 'vector'
      AND NOT attribute.attisdropped;

    IF current_vector_type <> 'vector(1024)' THEN
        ALTER TABLE doc_search.embeddings
            ALTER COLUMN vector TYPE vector(1024);
    END IF;
END;
$$;

COMMENT ON COLUMN doc_search.embeddings.vector IS
    '1024-dimensional qwen3-embedding:0.6b vector.';

INSERT INTO doc_search.embedding_models (
    model_name, model_version, provider, dimensions, max_tokens, is_active, notes
) VALUES (
    'qwen3-embedding', '0.6b', 'ollama', 1024, 32768, TRUE,
    'Open-weight Qwen3 Embedding 0.6B served locally through Ollama'
)
ON CONFLICT (model_name, model_version) DO UPDATE
SET provider = EXCLUDED.provider,
    dimensions = EXCLUDED.dimensions,
    max_tokens = EXCLUDED.max_tokens,
    is_active = TRUE,
    notes = EXCLUDED.notes;

UPDATE doc_search.embedding_models
SET is_active = FALSE
WHERE provider <> 'ollama'
  AND is_active = TRUE;

CREATE VIEW doc_search.active_document_chunks AS
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

CREATE INDEX idx_embeddings_hnsw
    ON doc_search.embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE FUNCTION doc_search.search_documents(
    p_query_vector vector(1024),
    p_top_k INTEGER DEFAULT 5,
    p_category VARCHAR DEFAULT NULL,
    p_min_score FLOAT DEFAULT 0.0
) RETURNS TABLE (
    chunk_id UUID,
    chunk_text TEXT,
    document_id UUID,
    document_title VARCHAR,
    version_number INTEGER,
    page_number INTEGER,
    section_title VARCHAR,
    similarity FLOAT
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
        (1 - (adc.vector <=> p_query_vector))::FLOAT
    FROM doc_search.active_document_chunks adc
    WHERE (p_category IS NULL OR adc.category = p_category)
      AND (1 - (adc.vector <=> p_query_vector)) >= p_min_score
    ORDER BY adc.vector <=> p_query_vector
    LIMIT p_top_k;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON VIEW doc_search.active_document_chunks IS
    'Traceable chunks and vectors from ACTIVE, non-deleted documents.';
COMMENT ON FUNCTION doc_search.search_documents(vector, INTEGER, VARCHAR, FLOAT) IS
    'Cosine ANN search for 1024-dimensional Qwen3 embeddings.';

COMMIT;
