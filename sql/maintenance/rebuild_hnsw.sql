\set ON_ERROR_STOP on

-- Override with: psql -v hnsw_m=24 -v hnsw_ef_construction=128 -f ...
\if :{?hnsw_m}
\else
\set hnsw_m 16
\endif
\if :{?hnsw_ef_construction}
\else
\set hnsw_ef_construction 64
\endif

-- Build the replacement first so searches retain the existing index during construction.
DROP INDEX CONCURRENTLY IF EXISTS doc_search.idx_embeddings_hnsw_next;
CREATE INDEX CONCURRENTLY idx_embeddings_hnsw_next
    ON doc_search.embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = :hnsw_m, ef_construction = :hnsw_ef_construction);

BEGIN;
DROP INDEX IF EXISTS doc_search.idx_embeddings_hnsw;
ALTER INDEX doc_search.idx_embeddings_hnsw_next RENAME TO idx_embeddings_hnsw;
COMMIT;

ANALYZE doc_search.embeddings;
