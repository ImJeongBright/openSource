from __future__ import annotations

import json
import logging

from src.logging_config import JsonFormatter


def test_json_formatter_includes_trace_fields_without_arbitrary_extras() -> None:
    record = logging.LogRecord(
        name="opensql.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="processed %s",
        args=("document",),
        exc_info=None,
    )
    record.document_id = "document-id"
    record.version_id = "version-id"
    record.stage = "embed"
    record.password = "must-not-be-serialized"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "processed document"
    assert payload["document_id"] == "document-id"
    assert payload["version_id"] == "version-id"
    assert payload["stage"] == "embed"
    assert "password" not in payload
