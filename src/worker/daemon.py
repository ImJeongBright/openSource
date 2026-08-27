from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import UUID, uuid4

from src.config import settings
from src.db import db
from src.logging_config import get_json_logger


logger = get_json_logger(__name__)

Job = Mapping[str, Any]
JobHandler = Callable[[Job], Awaitable[None]]


class WorkerError(RuntimeError):
    """Base exception for worker lifecycle failures."""


class WorkerLeaseLostError(WorkerError):
    """Raised when a worker no longer owns a processing job."""


CLAIM_JOB_SQL = """
WITH candidate AS (
    SELECT id
    FROM doc_search.change_log
    WHERE status = 'PENDING'
      AND retry_count < max_retries
      AND updated_at <= NOW() - CASE retry_count
          WHEN 0 THEN INTERVAL '0 seconds'
          WHEN 1 THEN INTERVAL '30 seconds'
          WHEN 2 THEN INTERVAL '120 seconds'
          ELSE INTERVAL '300 seconds'
      END
    ORDER BY created_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE doc_search.change_log AS cl
SET status = 'PROCESSING',
    worker_id = $1,
    locked_at = NOW(),
    updated_at = NOW(),
    error_message = NULL,
    error_detail = NULL
FROM candidate
WHERE cl.id = candidate.id
RETURNING cl.*
"""

MARK_VERSION_PROCESSING_SQL = """
UPDATE doc_search.document_versions
SET status = 'PROCESSING',
    processing_started_at = COALESCE(processing_started_at, NOW()),
    updated_at = NOW(),
    error_message = NULL
WHERE id = $1
  AND document_id = $2
  AND status = 'PENDING'
"""

LOCKED_VERSION_STATUS_SQL = """
SELECT status::text
FROM doc_search.document_versions
WHERE id = $1 AND document_id = $2
FOR UPDATE
"""

HEARTBEAT_SQL = """
UPDATE doc_search.change_log
SET locked_at = NOW(), updated_at = NOW()
WHERE id = $1
  AND status = 'PROCESSING'
  AND worker_id = $2
RETURNING id
"""

MARK_COMPLETED_SQL = """
UPDATE doc_search.change_log
SET status = 'COMPLETED',
    worker_id = NULL,
    locked_at = NULL,
    updated_at = NOW(),
    completed_at = NOW()
WHERE id = $1
  AND status = 'PROCESSING'
  AND worker_id = $2
RETURNING id
"""

MARK_FAILED_SQL = """
UPDATE doc_search.change_log
SET status = $3::doc_search.job_status,
    retry_count = $4,
    worker_id = NULL,
    locked_at = NULL,
    updated_at = NOW(),
    completed_at = CASE WHEN $3 = 'DEAD_LETTER' THEN NOW() ELSE NULL END,
    error_message = $5,
    error_detail = $6::jsonb
WHERE id = $1
  AND status = 'PROCESSING'
  AND worker_id = $2
RETURNING id
"""

FIND_STALE_JOBS_SQL = """
SELECT id, version_id
FROM doc_search.change_log
WHERE status = 'PROCESSING'
  AND locked_at IS NOT NULL
  AND locked_at < NOW() - make_interval(mins => $1)
ORDER BY locked_at ASC, id ASC
FOR UPDATE SKIP LOCKED
LIMIT 100
"""

RELEASE_STALE_JOB_SQL = """
UPDATE doc_search.change_log
SET status = 'PENDING',
    worker_id = NULL,
    locked_at = NULL,
    updated_at = NOW(),
    error_message = COALESCE(error_message, 'worker lease expired')
WHERE id = $1
  AND status = 'PROCESSING'
"""

RESET_VERSION_AFTER_LEASE_SQL = """
UPDATE doc_search.document_versions
SET status = 'PENDING', updated_at = NOW()
WHERE id = $1 AND status = 'PROCESSING'
"""

UPDATE_VERSION_AFTER_FAILURE_SQL = """
UPDATE doc_search.document_versions
SET status = $2::doc_search.version_status,
    updated_at = NOW(),
    error_message = $3
WHERE id = $1 AND status = 'PROCESSING'
"""


@dataclass(frozen=True)
class WorkerConfig:
    """Runtime values copied from settings so a Worker is testable in isolation."""

    poll_interval_seconds: float
    lock_timeout_minutes: int
    heartbeat_interval_seconds: float

    @classmethod
    def from_settings(cls) -> "WorkerConfig":
        lock_timeout = max(1, int(settings.WORKER_LOCK_TIMEOUT_MINUTES))
        return cls(
            poll_interval_seconds=max(0.1, float(settings.WORKER_POLL_INTERVAL_SECONDS)),
            lock_timeout_minutes=lock_timeout,
            heartbeat_interval_seconds=max(1.0, min(60.0, lock_timeout * 60 / 3)),
        )


def _job_id(job: Job) -> int:
    value = job.get("id")
    if value is None:
        raise WorkerError("claimed job has no id")
    return int(value)


def _version_id(job: Job) -> Optional[UUID]:
    value = job.get("version_id")
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _document_id(job: Job) -> Optional[UUID]:
    value = job.get("document_id")
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _error_message(error: BaseException) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:4000]


class Worker:
    """Owns change_log leases and the worker state machine.

    The document-processing callback remains injectable for tests and custom
    jobs. The default handler is the complete Phase 7 document pipeline.
    """

    def __init__(
        self,
        worker_id: Optional[str] = None,
        config: Optional[WorkerConfig] = None,
    ) -> None:
        self.worker_id = worker_id or str(uuid4())
        self.config = config or WorkerConfig.from_settings()

    async def claim_job(self) -> Optional[Job]:
        """Atomically claim the oldest eligible PENDING job and its version."""
        async with db.connection() as connection:
            async with connection.transaction():
                job = await connection.fetchrow(CLAIM_JOB_SQL, self.worker_id)
                if job is None:
                    return None

                version_id = _version_id(job)
                document_id = _document_id(job)
                if version_id is not None and document_id is not None:
                    result = await connection.execute(
                        MARK_VERSION_PROCESSING_SQL,
                        version_id,
                        document_id,
                    )
                    if result != "UPDATE 1":
                        version_status = await connection.fetchval(
                            LOCKED_VERSION_STATUS_SQL,
                            version_id,
                            document_id,
                        )
                        if version_status in {"ACTIVE", "ARCHIVED"}:
                            # Activation may have committed before this job
                            # lost its lease. Do not reprocess an already
                            # published version; close the stale job safely.
                            completed = await connection.execute(
                                MARK_COMPLETED_SQL,
                                _job_id(job),
                                self.worker_id,
                            )
                            if completed == "UPDATE 1":
                                return None
                        raise WorkerLeaseLostError(
                            f"version {version_id} was not PENDING when job {_job_id(job)} was claimed"
                        )
                return job

    async def heartbeat(self, job: Job) -> None:
        """Refresh a lease only when this Worker still owns it."""
        async with db.connection() as connection:
            row = await connection.fetchrow(
                HEARTBEAT_SQL,
                _job_id(job),
                self.worker_id,
            )
        if row is None:
            raise WorkerLeaseLostError(f"worker lease lost for job {_job_id(job)}")

    async def recover_stale_jobs(self) -> int:
        """Return expired PROCESSING jobs to PENDING, including their versions."""
        recovered = 0
        async with db.connection() as connection:
            async with connection.transaction():
                stale_jobs = await connection.fetch(
                    FIND_STALE_JOBS_SQL,
                    self.config.lock_timeout_minutes,
                )
                for stale_job in stale_jobs:
                    result = await connection.execute(
                        RELEASE_STALE_JOB_SQL,
                        stale_job["id"],
                    )
                    if result != "UPDATE 1":
                        continue
                    version_id = stale_job["version_id"]
                    if version_id is not None:
                        await connection.execute(
                            RESET_VERSION_AFTER_LEASE_SQL,
                            version_id,
                        )
                    recovered += 1
        if recovered:
            logger.warning("Recovered %d expired worker leases", recovered)
        return recovered

    async def mark_completed(self, job: Job) -> None:
        """Complete a job without allowing an expired lease to overwrite state."""
        async with db.connection() as connection:
            row = await connection.fetchrow(
                MARK_COMPLETED_SQL,
                _job_id(job),
                self.worker_id,
            )
        if row is None:
            raise WorkerLeaseLostError(f"worker lease lost before completing job {_job_id(job)}")

    async def mark_failed(self, job: Job, error: BaseException) -> str:
        """Apply retry/backoff policy and update the version in the same transaction."""
        retry_count = int(job.get("retry_count") or 0) + 1
        configured_max_retries = job.get("max_retries")
        max_retries = (
            int(settings.MAX_RETRIES)
            if configured_max_retries is None
            else max(0, int(configured_max_retries))
        )
        new_status = "DEAD_LETTER" if retry_count >= max_retries else "PENDING"
        message = _error_message(error)
        detail = json.dumps(
            {"type": error.__class__.__name__, "worker_id": self.worker_id},
            ensure_ascii=False,
        )

        async with db.connection() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    MARK_FAILED_SQL,
                    _job_id(job),
                    self.worker_id,
                    new_status,
                    retry_count,
                    message,
                    detail,
                )
                if row is None:
                    raise WorkerLeaseLostError(
                        f"worker lease lost before failing job {_job_id(job)}"
                    )

                version_id = _version_id(job)
                if version_id is not None:
                    version_status = "FAILED" if new_status == "DEAD_LETTER" else "PENDING"
                    await connection.execute(
                        UPDATE_VERSION_AFTER_FAILURE_SQL,
                        version_id,
                        version_status,
                        message,
                    )

        logger.warning(
            "Worker job failed: id=%s retry=%s/%s status=%s error=%s",
            _job_id(job),
            retry_count,
            max_retries,
            new_status,
            message,
        )
        return new_status

    async def _heartbeat_loop(self, job: Job, stop_event: Optional[asyncio.Event]) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                await asyncio.sleep(self.config.heartbeat_interval_seconds)
                if stop_event is not None and stop_event.is_set():
                    return
                await self.heartbeat(job)
            except asyncio.CancelledError:
                raise
            except WorkerLeaseLostError as error:
                logger.warning("%s", error)
                return
            except Exception:
                # A transient DB error must not crash the heartbeat task. The
                # lease guard on completion/failure remains the final safety net.
                logger.exception("Worker heartbeat failed for job %s", _job_id(job))

    async def run_once(
        self,
        job_handler: JobHandler,
        stop_event: Optional[asyncio.Event] = None,
    ) -> bool:
        """Run one poll/process cycle. Return whether a job was claimed."""
        await self.recover_stale_jobs()
        job = await self.claim_job()
        if job is None:
            return False

        started = time.perf_counter()
        trace = {
            "job_id": _job_id(job),
            "document_id": _document_id(job),
            "version_id": _version_id(job),
            "worker_id": self.worker_id,
        }
        logger.info("worker job started", extra={**trace, "stage": "worker"})
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(job, stop_event))
        try:
            await job_handler(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.mark_failed(job, error)
            logger.warning(
                "worker job scheduled for retry or dead letter",
                extra={
                    **trace,
                    "stage": "worker",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error_code": type(error).__name__,
                },
            )
        else:
            await self.mark_completed(job)
            logger.info(
                "worker job completed",
                extra={
                    **trace,
                    "stage": "worker",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        return True

    async def run(
        self,
        job_handler: JobHandler,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Poll until stop_event is set, tolerating transient DB errors."""
        while stop_event is None or not stop_event.is_set():
            try:
                claimed = await self.run_once(job_handler, stop_event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker poll cycle failed")
                claimed = False

            if claimed:
                continue
            if stop_event is None:
                await asyncio.sleep(self.config.poll_interval_seconds)
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.config.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass


async def process_job(job: Job) -> None:
    """Run the default extract -> chunk -> embed -> activate pipeline."""
    from src.pipeline.runner import process_document_job

    await process_document_job(job)


async def run_worker(
    job_handler: JobHandler = process_job,
    worker_id: Optional[str] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Run a configured Worker daemon."""
    worker = Worker(worker_id=worker_id)
    await worker.run(job_handler, stop_event)


if __name__ == "__main__":
    asyncio.run(run_worker())
