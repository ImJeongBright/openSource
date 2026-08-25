from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api import routes
from src.api.routes import RegisteredUpload
from src.config import settings
from src.models import (
    DocumentDetailResponse,
    DocumentListItem,
    ProcessingStatusResponse,
    VersionInfo,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return TestClient(routes.app)


def test_upload_success_stages_hashes_and_cleans_temp_file(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    version_id = uuid4()
    observed = {}

    async def fake_register(**kwargs):
        staged = kwargs["staged"]
        observed["staged"] = staged
        observed["kwargs"] = kwargs
        assert staged.path.exists()
        return RegisteredUpload(document_id, version_id, 1, tmp_path / f"{version_id}.md")

    monkeypatch.setattr(routes, "_register_upload", fake_register)
    response = client.post(
        "/api/documents",
        files={"file": ("guide.markdown", b"# OpenSQL", "text/markdown")},
        data={
            "title": "  운영 가이드  ",
            "category": " database ",
            "tags": "OpenSQL,검색,OpenSQL",
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_id"] == str(document_id)
    assert payload["version_id"] == str(version_id)
    assert payload["status"] == "PENDING"
    assert payload["file_hash"] == observed["staged"].file_hash
    assert observed["staged"].file_type == "markdown"
    assert observed["staged"].extension == ".md"
    assert observed["kwargs"]["title"] == "운영 가이드"
    assert observed["kwargs"]["category"] == "database"
    assert observed["kwargs"]["tags"] == ["OpenSQL", "검색"]
    assert list(tmp_path.glob("upload-*.tmp")) == []


@pytest.mark.parametrize("filename", ["image.png", "archive.zip", "no_extension"])
def test_upload_rejects_unsupported_files(client: TestClient, filename: str) -> None:
    response = client.post(
        "/api/documents",
        files={"file": (filename, b"content", "application/octet-stream")},
        data={"title": "unsupported"},
    )
    assert response.status_code == 415


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = client.post(
        "/api/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
        data={"title": "empty"},
    )
    assert response.status_code == 400


def test_upload_rejects_file_over_limit(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "MAX_FILE_SIZE_MB", 1)
    response = client.post(
        "/api/documents",
        files={"file": ("large.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
        data={"title": "large"},
    )
    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_upload_maps_duplicate_to_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def duplicate(**kwargs):
        raise routes.DuplicateUploadError("duplicate")

    monkeypatch.setattr(routes, "_register_upload", duplicate)
    response = client.post(
        "/api/documents",
        files={"file": ("same.txt", b"same", "text/plain")},
        data={"title": "same"},
    )
    assert response.status_code == 409


def test_list_documents_returns_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = DocumentListItem(
        document_id=uuid4(),
        title="문서",
        file_type="pdf",
        created_at=datetime.now(timezone.utc),
        latest_version_number=1,
        latest_version_status="ACTIVE",
    )

    async def fake_list(*args):
        return [item], 1

    monkeypatch.setattr(routes, "_list_documents", fake_list)
    response = client.get("/api/documents?limit=10&offset=0")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "문서"


def test_list_documents_rejects_reversed_date_range(client: TestClient) -> None:
    response = client.get(
        "/api/documents",
        params={
            "created_after": "2026-08-27T00:00:00Z",
            "created_before": "2026-08-26T00:00:00Z",
        },
    )
    assert response.status_code == 422


def test_get_document_and_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = uuid4()
    version_id = uuid4()
    now = datetime.now(timezone.utc)
    detail = DocumentDetailResponse(
        document_id=document_id,
        title="가이드",
        file_type="pdf",
        created_at=now,
        updated_at=now,
        versions=[
            VersionInfo(
                version_id=version_id,
                version_number=1,
                status="PROCESSING",
                total_chunks=10,
                embedded_chunks=4,
                created_at=now,
            )
        ],
    )
    processing = ProcessingStatusResponse(
        document_id=document_id,
        document_title="가이드",
        version_id=version_id,
        version_number=1,
        version_status="PROCESSING",
        total_chunks=10,
        embedded_chunks=4,
        embedding_progress_pct=40.0,
        job_status="PROCESSING",
    )

    async def fake_detail(value: UUID):
        assert value == document_id
        return detail

    async def fake_status(value: UUID):
        assert value == document_id
        return processing

    monkeypatch.setattr(routes, "_fetch_document_detail", fake_detail)
    monkeypatch.setattr(routes, "_fetch_status", fake_status)

    detail_response = client.get(f"/api/documents/{document_id}")
    status_response = client.get(f"/api/documents/{document_id}/status")
    assert detail_response.status_code == 200
    assert detail_response.json()["versions"][0]["status"] == "PROCESSING"
    assert status_response.status_code == 200
    assert status_response.json()["embedding_progress_pct"] == 40.0


def test_get_document_returns_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(value: UUID):
        return None

    monkeypatch.setattr(routes, "_fetch_document_detail", missing)
    response = client.get(f"/api/documents/{uuid4()}")
    assert response.status_code == 404


def test_model_registry_identity() -> None:
    assert routes._model_registry_identity() == ("qwen3-embedding", "0.6b")
