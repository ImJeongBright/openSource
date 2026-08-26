from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from src.worker import daemon


class Transaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None


class FakeConnection:
    def __init__(
        self,
        job=None,
        stale_jobs=None,
        update_results=None,
        version_status="PENDING",
    ) -> None:
        self.job = job
        self.stale_jobs = stale_jobs or []
        self.update_results = list(update_results or [])
        self.version_status = version_status
        self.transaction_state = Transaction()
        self.fetchrow_queries = []
        self.fetch_queries = []
        self.execute_calls = []

    def transaction(self):
        return self.transaction_state

    async def fetchrow(self, query, *arguments):
        self.fetchrow_queries.append((query, arguments))
        if "RETURNING cl.*" in query:
            return self.job
        if "RETURNING id" in query:
            return self.job if self.job is not None else None
        return None

    async def fetch(self, query, *arguments):
        self.fetch_queries.append((query, arguments))
        return self.stale_jobs

    async def fetchval(self, query, *arguments):
        return self.version_status

    async def execute(self, query, *arguments):
        self.execute_calls.append((query, arguments))
        if self.update_results:
            return self.update_results.pop(0)
        return "UPDATE 1"


def install_connection(monkeypatch: pytest.MonkeyPatch, connection: FakeConnection) -> None:
    @asynccontextmanager
    async def fake_connection():
        yield connection

    monkeypatch.setattr(daemon.db, "connection", fake_connection)


def job(**overrides):
    value = {
        "id": 11,
        "document_id": uuid4(),
        "version_id": uuid4(),
        "retry_count": 0,
        "max_retries": 3,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_claim_updates_job_and_version_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed = job()
    connection = FakeConnection(claimed)
    install_connection(monkeypatch, connection)
    worker = daemon.Worker(worker_id="worker-a")

    result = await worker.claim_job()

    assert result == claimed
    assert connection.transaction_state.committed is True
    assert "FOR UPDATE SKIP LOCKED" in connection.fetchrow_queries[0][0]
    assert "retry_count < max_retries" in connection.fetchrow_queries[0][0]
    assert connection.fetchrow_queries[0][1] == ("worker-a",)
    assert "document_versions" in connection.execute_calls[0][0]
    assert connection.execute_calls[0][1] == (
        claimed["version_id"],
        claimed["document_id"],
    )


@pytest.mark.asyncio
async def test_claim_returns_none_when_no_eligible_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeConnection(None)
    install_connection(monkeypatch, connection)

    assert await daemon.Worker(worker_id="worker-a").claim_job() is None
    assert connection.transaction_state.committed is True
    assert connection.execute_calls == []


@pytest.mark.asyncio
async def test_claim_closes_job_when_version_was_activated_before_lease_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed = job()
    connection = FakeConnection(
        claimed,
        update_results=["UPDATE 0", "UPDATE 1"],
        version_status="ACTIVE",
    )
    install_connection(monkeypatch, connection)

    result = await daemon.Worker(worker_id="worker-a").claim_job()

    assert result is None
    assert connection.transaction_state.committed is True
    assert "document_versions" in connection.execute_calls[0][0]
    assert "status = 'COMPLETED'" in connection.execute_calls[1][0]


@pytest.mark.asyncio
async def test_heartbeat_requires_current_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    current_job = job(id=12)
    connection = FakeConnection(current_job)
    install_connection(monkeypatch, connection)
    worker = daemon.Worker(worker_id="worker-a")

    await worker.heartbeat(current_job)
    assert connection.fetchrow_queries[0][1] == (12, "worker-a")

    connection.job = None
    with pytest.raises(daemon.WorkerLeaseLostError):
        await worker.heartbeat(current_job)


@pytest.mark.asyncio
async def test_transient_failure_returns_job_to_pending_and_version_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_job = job(retry_count=1)
    connection = FakeConnection(failed_job)
    install_connection(monkeypatch, connection)
    worker = daemon.Worker(worker_id="worker-a")

    status = await worker.mark_failed(failed_job, TimeoutError("embedding timeout"))

    assert status == "PENDING"
    assert connection.transaction_state.committed is True
    failed_call = connection.fetchrow_queries[0]
    assert "retry_count = $4" in failed_call[0]
    assert failed_call[1][2:5] == ("PENDING", 2, "embedding timeout")
    assert connection.execute_calls[0][1][1:] == (
        "PENDING",
        "embedding timeout",
    )


@pytest.mark.asyncio
async def test_final_failure_moves_job_to_dead_letter_and_version_to_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_job = job(retry_count=2)
    connection = FakeConnection(failed_job)
    install_connection(monkeypatch, connection)
    worker = daemon.Worker(worker_id="worker-a")

    status = await worker.mark_failed(failed_job, ValueError("bad document"))

    assert status == "DEAD_LETTER"
    failed_call = connection.fetchrow_queries[0]
    assert failed_call[1][2:5] == ("DEAD_LETTER", 3, "bad document")
    assert connection.execute_calls[0][1][1:] == ("FAILED", "bad document")


@pytest.mark.asyncio
async def test_stale_processing_job_and_version_are_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = job(id=99)
    connection = FakeConnection(None, stale_jobs=[stale])
    install_connection(monkeypatch, connection)
    worker = daemon.Worker(worker_id="worker-a")

    recovered = await worker.recover_stale_jobs()

    assert recovered == 1
    assert connection.transaction_state.committed is True
    assert "make_interval" in connection.fetch_queries[0][0]
    assert connection.execute_calls[0][1] == (99,)
    assert connection.execute_calls[1][1] == (stale["version_id"],)


@pytest.mark.asyncio
async def test_run_once_marks_successful_handler_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = daemon.Worker(worker_id="worker-a")
    claimed = job()
    calls = []

    async def fake_recover():
        calls.append("recover")
        return 0

    async def fake_claim():
        calls.append("claim")
        return claimed

    async def fake_heartbeat_loop(*args, **kwargs):
        await daemon.asyncio.Event().wait()

    async def handler(received):
        calls.append(("handle", received))

    async def fake_completed(received):
        calls.append(("completed", received))

    monkeypatch.setattr(worker, "recover_stale_jobs", fake_recover)
    monkeypatch.setattr(worker, "claim_job", fake_claim)
    monkeypatch.setattr(worker, "_heartbeat_loop", fake_heartbeat_loop)
    monkeypatch.setattr(worker, "mark_completed", fake_completed)

    assert await worker.run_once(handler) is True
    assert calls[:2] == ["recover", "claim"]
    assert calls[2] == ("handle", claimed)
    assert calls[3] == ("completed", claimed)


@pytest.mark.asyncio
async def test_run_once_marks_handler_error_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = daemon.Worker(worker_id="worker-a")
    claimed = job()
    observed = {}

    async def fake_recover():
        return 0

    async def fake_claim():
        return claimed

    async def fake_heartbeat_loop(*args, **kwargs):
        await daemon.asyncio.Event().wait()

    async def handler(received):
        raise RuntimeError("temporary failure")

    async def fake_failed(received, error):
        observed["job"] = received
        observed["error"] = error
        return "PENDING"

    monkeypatch.setattr(worker, "recover_stale_jobs", fake_recover)
    monkeypatch.setattr(worker, "claim_job", fake_claim)
    monkeypatch.setattr(worker, "_heartbeat_loop", fake_heartbeat_loop)
    monkeypatch.setattr(worker, "mark_failed", fake_failed)

    assert await worker.run_once(handler) is True
    assert observed["job"] == claimed
    assert str(observed["error"]) == "temporary failure"


def test_worker_config_uses_safe_heartbeat_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon.settings, "WORKER_LOCK_TIMEOUT_MINUTES", 10)
    monkeypatch.setattr(daemon.settings, "WORKER_POLL_INTERVAL_SECONDS", 5)

    config = daemon.WorkerConfig.from_settings()

    assert config.lock_timeout_minutes == 10
    assert config.poll_interval_seconds == 5
    assert config.heartbeat_interval_seconds == 60
