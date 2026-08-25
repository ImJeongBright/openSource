-- Application tables for a clean Qwen3 Embedding (1024d) installation.

CREATE TABLE doc_search.embedding_models (
    id            SERIAL        PRIMARY KEY,
    model_name    VARCHAR(100)  NOT NULL,
    model_version VARCHAR(50)   NOT NULL,
    provider      VARCHAR(50)   NOT NULL,
    dimensions    INTEGER       NOT NULL,
    max_tokens    INTEGER,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    notes         TEXT,
    CONSTRAINT uq_model_name_version UNIQUE (model_name, model_version),
    CONSTRAINT chk_embedding_dimensions CHECK (dimensions > 0)
);

ALTER TABLE doc_search.document_versions
    ADD CONSTRAINT fk_doc_versions_embedding_model
    FOREIGN KEY (embedding_model_id) REFERENCES doc_search.embedding_models(id);

CREATE TABLE doc_search.embeddings (
    id                 UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    chunk_id           UUID         NOT NULL UNIQUE
                       REFERENCES doc_search.chunks(id) ON DELETE CASCADE,
    version_id         UUID         NOT NULL
                       REFERENCES doc_search.document_versions(id) ON DELETE CASCADE,
    document_id        UUID         NOT NULL
                       REFERENCES doc_search.documents(id) ON DELETE CASCADE,
    embedding_model_id INTEGER      NOT NULL
                       REFERENCES doc_search.embedding_models(id),
    vector             vector(1024) NOT NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE doc_search.embedding_models IS
    'Open-weight embedding model registry and reproducibility metadata.';
COMMENT ON TABLE doc_search.embeddings IS
    'Qwen3 embedding vectors with one embedding per chunk.';
COMMENT ON COLUMN doc_search.embeddings.vector IS
    '1024-dimensional qwen3-embedding:0.6b vector.';

CREATE TABLE doc_search.change_log (
    id            BIGSERIAL     PRIMARY KEY,
    event_type    doc_search.change_event_type NOT NULL,
    status        doc_search.job_status NOT NULL DEFAULT 'PENDING',
    document_id   UUID          NOT NULL,
    version_id    UUID,
    chunk_id      UUID,
    retry_count   INTEGER       NOT NULL DEFAULT 0,
    max_retries   INTEGER       NOT NULL DEFAULT 3,
    worker_id     VARCHAR(200),
    locked_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    error_message TEXT,
    error_detail  JSONB,
    CONSTRAINT chk_retry_count CHECK (retry_count >= 0),
    CONSTRAINT chk_max_retries CHECK (max_retries >= 0)
);

COMMENT ON TABLE doc_search.change_log IS
    'Append-only event history and Worker job state.';
