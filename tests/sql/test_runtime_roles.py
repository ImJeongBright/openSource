from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_role_sql_enforces_separate_least_privilege_accounts() -> None:
    sql = (ROOT / "sql/security/01_runtime_roles.sql").read_text(encoding="utf-8")

    assert "CREATE ROLE opensql_api LOGIN" in sql
    assert "CREATE ROLE opensql_worker LOGIN" in sql
    assert "CREATE ROLE mcp_app_user LOGIN" in sql
    assert "GRANT EXECUTE ON FUNCTION doc_search.activate_version(UUID)" in sql
    assert "GRANT SELECT\n    ON doc_search.documents" in sql
    assert "GRANT SELECT, INSERT ON doc_search.embeddings TO opensql_worker" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE\n    ON doc_search.documents" in sql


def test_systemd_services_load_role_specific_environment_overrides() -> None:
    api_unit = (ROOT / "opensql-api.service").read_text(encoding="utf-8")
    worker_unit = (ROOT / "opensql-worker.service").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/opensql-doc-search/api.env" in api_unit
    assert "EnvironmentFile=/etc/opensql-doc-search/worker.env" in worker_unit
