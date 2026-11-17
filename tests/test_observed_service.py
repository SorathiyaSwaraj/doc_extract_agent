"""Phase 8: what the running service reports about itself."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from doc_extract.observability.cost import Budget, PriceTable
from doc_extract.observability.metrics import ServiceMetrics
from doc_extract.service.api import create_app
from doc_extract.service.config import Settings
from doc_extract.service.pipeline import Call, Outcome
from doc_extract.service.store import MIGRATIONS, SCHEMA, Store
from doc_extract.service.worker import Worker, start_of_day

KEY = "alice-key-0123456789"
HEADERS = {"X-API-Key": KEY}


def outcome_with_cost(decision="review", invoice=None):
    def process(pdf, doc_id):
        outcome = Outcome(doc_id, "v", invoice, decision, 0.35, ["r"], [], [])
        outcome.calls = [Call("primary", "vision", "gemini", "gemini-x", 1000, 500, 2.0)]
        outcome.stages = {"primary": 2.0, "decide": 0.01}
        return outcome

    return process


def test_old_database_is_migrated_in_place(tmp_path):
    db_path = tmp_path / "db.sqlite"
    old = sqlite3.connect(db_path)
    old.executescript(SCHEMA)  # what Phase 7 created, with no version recorded
    old.execute(
        "INSERT INTO documents (id, sha256, size, status, available_at, created_at, updated_at) "
        "VALUES ('a', 'h', 1, 'queued', '', '', '')"
    )
    old.commit()
    old.close()

    store = Store(db_path, tmp_path / "files")

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
    assert {"cost_usd", "seconds", "processed_at"} <= columns
    assert store.get("a")["status"] == "queued"  # data kept
    Store(db_path, tmp_path / "files")  # a second start changes nothing


def test_worker_prices_outcome_and_records_metrics(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-1", None)
    metrics = ServiceMetrics()
    prices = PriceTable.parse("gemini-x=1.0/2.0")

    Worker(store, outcome_with_cost(), prices=prices, metrics=metrics).run_once()

    document = store.get(doc_id)
    assert document["cost_usd"] == pytest.approx(0.002)  # 1000 * 1 + 500 * 2 per million
    assert document["seconds"] == pytest.approx(2.01)
    assert store.spent_since(start_of_day()) == pytest.approx(0.002)
    assert metrics.cost.value(model="gemini-x") == pytest.approx(0.002)


def test_worker_pauses_when_daily_budget_is_spent(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    first, _ = store.add(b"%PDF-1", None)
    second, _ = store.add(b"%PDF-2", None)
    worker = Worker(
        store,
        outcome_with_cost(),
        prices=PriceTable.parse("gemini-x=1.0/2.0"),
        budget=Budget(0.001),
    )

    assert worker.run_once() is True  # nothing spent yet, so the first document runs
    assert worker.run_once() is False  # 0.002 spent, over the 0.001 budget

    assert worker.paused_for_budget
    assert store.get(second)["status"] == "queued"


def test_failures_are_counted_by_kind(tmp_path):
    from doc_extract.service.pipeline import TransientError

    store = Store(tmp_path / "db", tmp_path / "files")
    store.add(b"%PDF-1", None)
    metrics = ServiceMetrics()

    def outage(pdf, doc_id):
        raise TransientError("503")

    Worker(store, outage, metrics=metrics, retry_base_seconds=0).run_once()

    assert metrics.failures.value(kind="transient") == 1


@pytest.fixture
def client_and_app(tmp_path, sample_invoice):
    invoice, _ = sample_invoice
    settings = Settings(
        db_path=tmp_path / "db",
        storage_dir=tmp_path / "files",
        api_keys={KEY: "alice"},
        prices=PriceTable.parse("gemini-x=1.0/2.0"),
    )
    process = outcome_with_cost(invoice=json.loads(invoice.model_dump_json()))
    app = create_app(settings, lambda settings, store: process, start_workers=False)
    app.state.process = process
    with TestClient(app) as client:
        yield client, app


def drain(app):
    """Stands in for the background worker, with the app's prices and metrics."""
    worker = Worker(
        app.state.store,
        app.state.process,
        prices=app.state.settings.prices,
        metrics=app.state.metrics,
    )
    while worker.run_once():
        pass


def upload(client, pdf):
    response = client.post("/v1/documents", headers=HEADERS, files={"file": ("a.pdf", pdf)})
    return response.json()["id"]


def test_request_id_is_returned_and_honoured(client_and_app):
    client, _ = client_and_app

    generated = client.get("/health").headers["X-Request-ID"]
    given = client.get("/health", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"]
    unsafe = client.get("/health", headers={"X-Request-ID": "a b\n"}).headers["X-Request-ID"]

    assert len(generated) == 32 and given == "abc-123" and unsafe != "a b\n"


def test_metrics_use_route_templates_and_need_a_key(client_and_app, sample_invoice):
    client, _ = client_and_app
    _, pdf = sample_invoice
    doc_id = upload(client, pdf)
    client.get(f"/v1/documents/{doc_id}", headers=HEADERS)

    assert client.get("/metrics").status_code == 401
    text = client.get("/metrics", headers=HEADERS).text

    assert 'route="/v1/documents/{doc_id}"' in text
    assert doc_id not in text  # no series per document
    assert 'doc_extract_uploads_total{new="true"} 1' in text
    assert 'doc_extract_documents{status="queued"} 1' in text


def test_stats_report_automation_corrections_and_cost(client_and_app, sample_invoice):
    client, app = client_and_app
    _, pdf = sample_invoice
    doc_id = upload(client, pdf)
    drain(app)
    client.post(
        f"/v1/documents/{doc_id}/review",
        headers=HEADERS,
        json={"action": "reject", "corrections": {"invoice_number": "X-9"}},
    )

    stats = client.get("/v1/stats", headers=HEADERS).json()

    assert stats["decided"] == 1 and stats["auto_approved"] == 0
    assert stats["automation_rate"] == 0
    assert stats["reviewed"] == 1 and stats["correction_rate"] == 1
    assert stats["fields_corrected"] == 1
    assert stats["cost_usd_since"] == pytest.approx(0.002)
    assert stats["cost_per_document_usd"] == pytest.approx(0.002)
    assert stats["seconds_p50"] == pytest.approx(2.01)
    assert stats["paused_for_budget"] is False
    metrics = client.get("/metrics", headers=HEADERS).text
    assert 'doc_extract_reviews_total{action="reject"} 1' in metrics
    assert "doc_extract_corrected_fields_total 1" in metrics
