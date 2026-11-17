"""Structured logs: one JSON object per line, with the request and document they belong to.

A log line that says "grand total check failed" is useless at 3 a.m. without the
document it was about. Context variables carry the request id and document id
through every call below the point where they are set, including into worker
threads that set their own, so no function has to pass them along by hand.

Logs never contain document content: no extracted values, no page text, no model
output. Ids, codes, counts, timings, and costs are enough to find a problem, and
the document itself is one authorised API call away.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
document_id: ContextVar[str | None] = ContextVar("document_id", default=None)

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, var in (("request_id", request_id), ("doc_id", document_id)):
            if (value := var.get()) is not None:
                entry[name] = value
        # Anything passed as logging's `extra=` becomes a field of its own.
        entry.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO", json_lines: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_lines:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Our request log replaces uvicorn's access log, which has no request id.
    logging.getLogger("uvicorn.access").disabled = True
