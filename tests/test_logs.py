import json
import logging

from doc_extract.observability.logs import JsonFormatter, document_id, request_id


def record(message, **extra):
    entry = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": message})
    entry.__dict__.update(extra)
    return entry


def test_one_json_object_with_context_and_extras():
    rid, did = request_id.set("r1"), document_id.set("d1")
    try:
        line = JsonFormatter().format(record("decided %s", seconds=1.5))
    finally:
        request_id.reset(rid)
        document_id.reset(did)

    entry = json.loads(line)
    assert entry["message"] == "decided %s"
    assert entry["request_id"] == "r1" and entry["doc_id"] == "d1"
    assert entry["seconds"] == 1.5 and entry["level"] == "info"


def test_context_is_absent_outside_a_request():
    entry = json.loads(JsonFormatter().format(record("startup")))

    assert "request_id" not in entry and "doc_id" not in entry


def test_exceptions_are_kept():
    try:
        raise ValueError("bad page")
    except ValueError:
        import sys

        entry = logging.makeLogRecord(
            {"name": "t", "levelname": "ERROR", "msg": "failed", "exc_info": sys.exc_info()}
        )

    assert "ValueError: bad page" in json.loads(JsonFormatter().format(entry))["error"]
