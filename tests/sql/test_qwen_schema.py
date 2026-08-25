from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_clean_install_uses_qwen_1024_dimensions() -> None:
    tables = _read("sql/06_app_tables.sql")
    functions = _read("sql/05_functions.sql")
    seed = _read("sql/08_seed.sql")

    assert "vector(1024)" in tables
    assert "vector(1536)" not in tables
    assert "p_query_vector  vector(1024)" in functions
    assert "qwen3-embedding" in seed
    assert "'ollama'" in seed
    assert "text-embedding-3" not in seed


def test_migration_has_data_loss_and_concurrency_guards() -> None:
    migration = _read("sql/migrations/V001__switch_embedding_to_qwen3_1024.sql")

    assert "BEGIN;" in migration
    assert "COMMIT;" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "ACCESS EXCLUSIVE" in migration
    assert "embedding_count > 0" in migration
    assert "Migration stopped" in migration
    assert "DROP INDEX IF EXISTS doc_search.idx_embeddings_hnsw" in migration
    assert "USING hnsw" in migration
    assert "vector(1024)" in migration


def test_processing_status_view_uses_one_latest_open_job() -> None:
    view = _read("sql/07_views.sql")
    migration = _read("sql/migrations/V002__deduplicate_processing_status_view.sql")

    for sql in (view, migration):
        assert "LEFT JOIN LATERAL" in sql
        assert "ORDER BY latest.created_at DESC" in sql
        assert "LIMIT 1" in sql
