# AGENTS.md — Project Rules & Conventions for AI Agents

This file defines the ground rules for any AI agent or developer working on this project.
Read this before making any changes to code, schema, documentation, or configuration.

---

## 1. Project Context

**Project**: OpenSQL AI Document Search & Version Management System  
**Stack**: Tmax OpenSQL 3.17.8.7 (PostgreSQL 17.8) + pgvector 0.8.1 + Python 3.10+ + MCP  
**Cluster HA**: Patroni 4.0.5 + etcd 3.6.5  
**OS**: Rocky Linux 9.7

The goal is to upload enterprise documents (PDF, TXT, Markdown), auto-process them through a
text extraction → chunking → embedding pipeline, store everything in a single OpenSQL database,
and expose semantic search via an MCP interface with full traceability (document name, version,
page number, source paragraph).

Design documents live in `docs/`. Read them before any structural change:

| File | Purpose |
|------|---------|
| `docs/01_project_definition.md` | Goals, scope, stakeholders, success criteria |
| `docs/02_usecase_specification.md` | UC-01 ~ UC-09 with flows and alternatives |
| `docs/03_functional_requirements.md` | FR-DOC / FR-PIPE / FR-STORE / FR-SEARCH / FR-MCP / FR-RECOVERY |
| `docs/04_non_functional_requirements.md` | Performance, availability, reliability, security targets |
| `docs/05_database_schema.md` | Full DDL, views, stored functions, index strategy |
| `docs/06_data_pipeline.md` | Step-by-step pipeline, Worker design, failure scenarios |

---

## 2. Directory Structure

```
opensql-doc-search/
├── .agents/
│   └── AGENTS.md          ← you are here
├── docs/                  ← design documents only, no code
├── sql/                   ← DDL scripts (init, migrations)
│   ├── init/              ← initial schema creation (idempotent)
│   └── migrations/        ← numbered migration files (V001__, V002__, ...)
├── src/                   ← application source code
│   ├── api/               ← upload API (FastAPI)
│   ├── pipeline/          ← Worker: extract → chunk → embed → activate
│   ├── search/            ← search service layer
│   └── common/            ← shared utilities (db, config, logging)
├── mcp/                   ← MCP server (search_documents, get_document, list_documents, get_chunk)
├── scripts/               ← evaluation, benchmark, MCP and demo entry points
├── samples/               ← non-sensitive demo documents
├── tests/                 ← pytest test files mirroring src/ structure
├── .env.example           ← template; never commit actual .env
├── .gitignore
└── README.md
```

**Rules**:
- Do NOT place application code inside `docs/`.
- Do NOT place SQL DDL inline in Python code — put it in `sql/`.
- Do NOT create new top-level directories without updating this file and `README.md`.

---

## 3. Git Commit Convention

Follow **Conventional Commits** strictly.

```
<type>(<scope>): <short summary in imperative mood>

[optional body — explain WHY, not what]
[optional footer — breaking changes, issue refs]
```

### Allowed types

| Type | When to use |
|------|------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation changes only |
| `sql` | Schema DDL or migration changes |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or updating tests |
| `chore` | Build, config, dependency updates |
| `perf` | Performance improvement |

### Scope examples
`api`, `pipeline`, `worker`, `search`, `mcp`, `schema`, `embed`, `docs`

### Examples
```
feat(pipeline): add exponential backoff for embedding API retries
fix(worker): release lock on PROCESSING timeout after 10 minutes
sql(schema): add embedding_model_id index on chunks table
docs(usecase): clarify UC-07 retry flow for DEAD_LETTER state
```

### Rules
- Subject line: 72 characters max, imperative mood, no period at end.
- Never use `git commit -m` with a vague message like `"fix"` or `"update"`.
- One logical change per commit. Do not batch unrelated changes.

---

## 4. Database Rules

### 4.1 Schema Ownership
- All tables live in the `doc_search` schema. Never use `public` for application tables.
- Always qualify table names: `doc_search.table_name`.

### 4.2 The One ACTIVE Version Rule
- Each document (`documents.id`) must have **at most one** version with `status = 'ACTIVE'`.
- This is enforced by `UNIQUE INDEX uq_one_active_version WHERE status = 'ACTIVE'`.
- **Never update version status directly in application code**. Always call `doc_search.activate_version(version_id)` to switch versions atomically.

### 4.3 Idempotency
- Embedding is idempotent: only process chunks where `is_embedded = FALSE`.
- Use `INSERT ... ON CONFLICT DO NOTHING` or `ON CONFLICT DO UPDATE` for embedding inserts.
- Never delete and re-insert embeddings during retry — update in place.

### 4.4 change_log is the Source of Truth
- Every state transition (upload, version switch, delete, embed fail) MUST be recorded in `doc_search.change_log`.
- Workers acquire jobs via `SELECT FOR UPDATE SKIP LOCKED` on `change_log`. Do not implement any other locking mechanism.
- Stale PROCESSING locks (older than `WORKER_LOCK_TIMEOUT_MINUTES`) must be reset to PENDING, not deleted.

### 4.5 DDL Changes
- All schema changes go in `sql/migrations/` as numbered files: `V001__add_chunk_language.sql`.
- Migrations must be idempotent (`IF NOT EXISTS`, `IF EXISTS`, `DO $$ ... EXCEPTION WHEN duplicate_column THEN NULL; END $$`).
- Never run `ALTER TABLE` directly on production without a migration file committed first.

### 4.6 pgvector Index
- Default index: **HNSW** with `m=16, ef_construction=64`, operator `vector_cosine_ops`.
- IVFFlat is only acceptable for local dev/prototype. Do not use it in production code paths.
- When rebuilding the index, search falls back to sequential scan automatically — document this in any ops runbook.

---

## 5. Pipeline Rules

### 5.1 Processing Stages (in order)
```
PENDING → PROCESSING → (extract) → (chunk) → (embed batches) → activate_version() → COMPLETED
```
- A version stays `PENDING` until a Worker acquires its `change_log` entry.
- A version stays `PROCESSING` until all `total_chunks == embedded_chunks`.
- **Never** set a version to `ACTIVE` unless `activate_version()` succeeds completely.

### 5.2 Embedding Batches
- Default batch size: 100 chunks per API call.
- Each batch is wrapped in its own DB transaction. Partial batch failure rolls back only that batch.
- After each successful batch: update `chunks.is_embedded = TRUE` and increment `document_versions.embedded_chunks`.

### 5.3 Embedding Model Versioning
- Always store `embedding_model_id` (FK to `doc_search.embedding_models`) on every chunk's embedding row.
- If the model changes, do **not** mix old and new vectors in the same search. Re-embed the entire document version before activating it.

### 5.4 Chunking Parameters
- `chunk_size` and `chunk_overlap` are stored per version in `document_versions`. Never hard-code them in pipeline logic — always read from the version record.

---

## 6. Security Rules

- **Secrets**: API keys, DB passwords, and tokens live only in `.env`. This file is gitignored. Use `.env.example` as the template.
- **Never** log or print API keys, passwords, or raw query vectors.
- DB connections for the pipeline Worker and MCP server must use **separate DB accounts** with minimum required privileges.
- TLS must be enabled for MCP server ↔ AI service communication in any non-local environment.
- Uploaded files are temporary. Clean them up after pipeline processing completes.

---

## 7. MCP Interface Rules

The MCP server exposes exactly four tools. Do not add tools without updating `docs/03_functional_requirements.md`.

| Tool | Required inputs | Guaranteed outputs |
|------|-----------------|--------------------|
| `search_documents` | `query` (str), optional `top_k`, `filters` | `chunk_text`, `document_title`, `version_number`, `page_number`, `section_title`, `similarity` |
| `get_document` | `document_id` | metadata, version info, chunk count |
| `list_documents` | optional `filters`, pagination | list of active documents |
| `get_chunk` | `chunk_id` | full chunk text + location (doc, version, page, section) |

- **All search results must include traceability fields** (`document_title`, `version_number`, `page_number`, `section_title`). Returning a chunk without these is a bug.
- Search must only return chunks from `ACTIVE` versions. Enforce this at the SQL level, not in application logic.

---

## 8. Code Style

- **Python**: Follow PEP 8. Use `ruff` for linting.
- **Type hints**: Required on all public functions and class methods.
- **Async**: Use `asyncio` + `asyncpg` for all DB operations in the pipeline and MCP server.
- **Error handling**: Catch specific exceptions. Never use bare `except:` or `except Exception as e: pass`.
- **Logging**: Use structured JSON logging. Every log entry from the pipeline must include `document_id`, `version_id`, and the current processing stage.
- **Config**: All tuneable parameters (chunk size, batch size, timeouts, model name) must be read from environment variables or a config file. No magic numbers in code.

---

## 9. Testing Rules

- Unit tests go in `tests/` mirroring the `src/` structure (e.g., `tests/pipeline/test_chunker.py`).
- Use `pytest`. Fixtures for DB interactions must use a dedicated test schema, never `doc_search`.
- Any function that touches `change_log` or calls `activate_version()` must have a test covering the failure path.
- Test idempotency: running the same pipeline job twice must produce identical DB state.

---

## 10. Non-Negotiable Invariants

These must hold at all times. Any code that violates them must be rejected:

1. **A document never has more than one ACTIVE version simultaneously.**
2. **No chunk's embedding is written to the `embeddings` table unless the parent `document_versions` row exists.**
3. **`activate_version()` is the only code path that sets `status = 'ACTIVE'`.**
4. **Search results always include `document_title`, `version_number`, `page_number`, and `section_title`.**
5. **`.env` is never committed. `.env.example` has no real values.**
6. **`change_log` entries are never deleted — only their status is updated.**
