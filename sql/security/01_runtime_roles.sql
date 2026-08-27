\set ON_ERROR_STOP on

-- Required psql variables: api_password, worker_password, mcp_password
SELECT format('CREATE ROLE opensql_api LOGIN PASSWORD %L', :'api_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opensql_api')
\gexec
SELECT format('CREATE ROLE opensql_worker LOGIN PASSWORD %L', :'worker_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opensql_worker')
\gexec
SELECT format('CREATE ROLE mcp_app_user LOGIN PASSWORD %L', :'mcp_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_app_user')
\gexec

SELECT format('ALTER ROLE opensql_api PASSWORD %L', :'api_password') \gexec
SELECT format('ALTER ROLE opensql_worker PASSWORD %L', :'worker_password') \gexec
SELECT format('ALTER ROLE mcp_app_user PASSWORD %L', :'mcp_password') \gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO opensql_api', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO opensql_worker', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO mcp_app_user', current_database()) \gexec

GRANT USAGE ON SCHEMA doc_search TO opensql_api, opensql_worker, mcp_app_user;
REVOKE CREATE ON SCHEMA doc_search FROM opensql_api, opensql_worker, mcp_app_user;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON doc_search.documents, doc_search.document_versions, doc_search.change_log
    TO opensql_api;
GRANT SELECT ON doc_search.embedding_models, doc_search.processing_status TO opensql_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA doc_search TO opensql_api;

-- SELECT ... FOR UPDATE in activate_version also requires UPDATE on documents.
GRANT SELECT, UPDATE ON doc_search.documents TO opensql_worker;
GRANT SELECT, UPDATE ON doc_search.document_versions TO opensql_worker;
GRANT SELECT, INSERT, UPDATE ON doc_search.change_log TO opensql_worker;
GRANT SELECT, INSERT, UPDATE ON doc_search.chunks TO opensql_worker;
GRANT SELECT, INSERT ON doc_search.embeddings TO opensql_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA doc_search TO opensql_worker;
GRANT EXECUTE ON FUNCTION doc_search.activate_version(UUID) TO opensql_worker;

GRANT SELECT
    ON doc_search.documents, doc_search.document_versions,
       doc_search.chunks, doc_search.embeddings
    TO mcp_app_user;
