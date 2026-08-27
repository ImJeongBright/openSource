from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.config import settings

TRACE_FIELDS = (
    "document_id",
    "version_id",
    "version_number",
    "job_id",
    "stage",
    "duration_ms",
    "total_chunks",
    "error_code",
    "worker_id",
)


class JsonFormatter(logging.Formatter):
    """Render application logs as one JSON object without serializing secrets."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in TRACE_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = str(value)
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_json_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(getattr(handler, "_opensql_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._opensql_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False
    return logger
