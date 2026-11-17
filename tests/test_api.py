import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from doc_extract.service.api import create_app
from doc_extract.service.config import Settings
from doc_extract.service.pipeline import Outcome

KEY = "alice-key-0123456789"
HEADERS = {"X-API-Key": KEY}


def fake_factory(decision="review", invoice=None):
    def factory(settings, store):
        def process(pdf, doc_id):
            reasons = [] if decision == "approve" else ["extractions disagree"]
            fields = [] if decision == "approve" else [{"field": "totals.grand_total"}]
            return Outcome(doc_id, "v", invoice, decision, 0.35, reasons, fields, [])

        return process

    return factory


@pytest.fixture
def make_client(tmp_path):
    def make(**factory_args):
        settings = Settings(
            db_path=tmp_path / "db.sqlite",
            storage_dir=tmp_path / "files",
            api_keys={KEY: "alice"},
            max_upload_bytes=200_000,
        )
        factory = fake_factory(**factory_args)
        app = create_app(settings, factory, start_workers=False)
        client = TestClient(app)
        client.process = lambda: _drain(app, factory)  # stands in for the background worker
        return client

    return make


def _drain(app, factory):
    from doc_extract.service.worker import Worker

    store = app.state.store
    worker = Worker(store, factory(app.state.settings, store))
    while worker.run_once():
        pass
    return True


def upload(client, pdf, name="invoice.pdf"):
    return client.post(
        "/v1/documents", headers=HEADERS, files={"file": (name, pdf, "application/pdf")}
    )


def test_health_needs_no_key(make_client):
    with make_client() as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 200


def test_requests_without_a_valid_key_are_refused(make_client):
    with make_client() as client:
        assert client.get("/v1/documents").status_code == 401
        assert client.get("/v1/documents", headers={"X-API-Key": "wrong"}).status_code == 401
        bearer = {"Authorization": f"Bearer {KEY}"}
        assert client.get("/v1/documents", headers=bearer).status_code == 200


def test_upload_process_and_read_result(make_client, sample_invoice):
    invoice, pdf = sample_invoice
    data = json.loads(invoice.model_dump_json())
    with make_client(invoice=data) as client:
        response = upload(client, pdf, "../../etc/inv.pdf")
        assert response.status_code == 202
        doc_id = response.json()["id"]
        assert client.get(f"/v1/documents/{doc_id}", headers=HEADERS).json()["status"] == "queued"

        client.process()

        document = client.get(f"/v1/documents/{doc_id}", headers=HEADERS).json()
        assert document["status"] == "done" and document["decision"] == "review"
        assert document["filename"] == "inv.pdf"
        assert document["invoice"]["invoice_number"] == invoice.invoice_number
        pdf_response = client.get(f"/v1/documents/{doc_id}/pdf", headers=HEADERS)
        assert pdf_response.content == pdf
        listed = client.get("/v1/documents?status=done", headers=HEADERS).json()
        assert [d["id"] for d in listed] == [doc_id]


def test_same_file_twice_returns_first_id(make_client, sample_invoice):
    _, pdf = sample_invoice
    with make_client() as client:
        first = upload(client, pdf).json()
        second = upload(client, pdf)

        assert second.status_code == 200
        assert second.json() == {**first, "duplicate_upload": True}


@pytest.mark.parametrize(
    ("content", "status"),
    [(b"hello", 415), (b"%PDF-1.7 broken", 422), (b"%PDF-" + b"x" * 300_000, 413)],
)
def test_bad_uploads_are_rejected(make_client, content, status):
    with make_client() as client:
        assert upload(client, content).status_code == status


def test_too_many_pages_rejected(make_client, sample_invoice):
    import pypdfium2 as pdfium

    _, pdf = sample_invoice
    merged = pdfium.PdfDocument.new()
    source = pdfium.PdfDocument(pdf)
    for _ in range(6):
        merged.import_pages(source)
    import io

    buffer = io.BytesIO()
    merged.save(buffer)
    with make_client() as client:
        response = upload(client, buffer.getvalue())

    assert response.status_code == 422 and "6 pages" in response.json()["detail"]


def test_review_with_correction_is_audited(make_client, sample_invoice):
    invoice, pdf = sample_invoice
    wrong = invoice.model_copy(
        update={"totals": invoice.totals.model_copy(update={"grand_total": Decimal("1.00")})}
    )
    with make_client(invoice=json.loads(wrong.model_dump_json())) as client:
        doc_id = upload(client, pdf).json()["id"]
        client.process()
        queue = client.get("/v1/review-queue", headers=HEADERS).json()
        assert [item["id"] for item in queue] == [doc_id]
        assert queue[0]["fields_to_check"] == [{"field": "totals.grand_total"}]

        refused = client.post(
            f"/v1/documents/{doc_id}/review", headers=HEADERS, json={"action": "approve"}
        )
        assert refused.status_code == 409  # still fails the grand-total check

        fixed = str(invoice.totals.grand_total)
        response = client.post(
            f"/v1/documents/{doc_id}/review",
            headers=HEADERS,
            json={"action": "approve", "corrections": {"totals.grand_total": fixed}},
        )

        assert response.status_code == 200
        document = response.json()
        assert document["final"] == "approved"
        assert document["invoice"]["totals"]["grand_total"] == fixed
        review = document["reviews"][0]
        assert review["reviewer"] == "alice"  # from the key, not from the request
        assert review["corrections"]["totals.grand_total"] == {"before": "1.00", "after": fixed}
        assert client.get("/v1/review-queue", headers=HEADERS).json() == []


def test_invalid_correction_is_422(make_client, sample_invoice):
    invoice, pdf = sample_invoice
    with make_client(invoice=json.loads(invoice.model_dump_json())) as client:
        doc_id = upload(client, pdf).json()["id"]
        client.process()

        response = client.post(
            f"/v1/documents/{doc_id}/review",
            headers=HEADERS,
            json={"action": "reject", "corrections": {"seller.phone": "1"}},
        )

    assert response.status_code == 422


def test_review_before_processing_is_409(make_client, sample_invoice):
    _, pdf = sample_invoice
    with make_client() as client:
        doc_id = upload(client, pdf).json()["id"]
        response = client.post(
            f"/v1/documents/{doc_id}/review", headers=HEADERS, json={"action": "reject"}
        )

    assert response.status_code == 409


def test_unknown_document_is_404(make_client):
    with make_client() as client:
        assert client.get("/v1/documents/nope", headers=HEADERS).status_code == 404


def test_pipeline_runs_end_to_end_through_the_api(tmp_path, sample_invoice):
    """The real pipeline behind the API, with a scripted model instead of Gemini."""
    from doc_extract.extraction.llm import Completion
    from doc_extract.service.pipeline import PipelineConfig, process_document

    invoice, pdf = sample_invoice

    class Model:
        name, model = "scripted", "m"

        def generate(self, prompt, schema, images=()):
            return Completion(invoice.model_dump_json(), self.name, self.model, 10, 5, 0.1)

    def factory(settings, store):
        return lambda pdf, doc_id: process_document(
            pdf,
            doc_id,
            Model(),
            None,
            PipelineConfig(),
            store.duplicate_index(),
            today=date(2026, 11, 16),
        )

    settings = Settings(
        db_path=tmp_path / "db", storage_dir=tmp_path / "files", allow_anonymous=True
    )
    app = create_app(settings, factory, start_workers=False)
    with TestClient(app) as client:
        doc_id = client.post("/v1/documents", files={"file": ("a.pdf", pdf)}).json()["id"]
        _drain(app, factory)
        document = client.get(f"/v1/documents/{doc_id}").json()

    assert document["decision"] == "approve" and document["final"] == "approved"
    assert [call["role"] for call in document["result"]["calls"]] == ["primary", "secondary"]


def test_broken_configuration_keeps_api_up_but_not_ready(tmp_path):
    def factory(settings, store):
        raise RuntimeError("Set GEMINI_API_KEY")

    settings = Settings(db_path=tmp_path / "db", storage_dir=tmp_path / "f", allow_anonymous=True)
    with TestClient(create_app(settings, factory)) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")

    assert ready.status_code == 503
    assert "Set GEMINI_API_KEY" in ready.json()["problems"][0]


def test_workers_start_and_stop_with_the_app(tmp_path):
    settings = Settings(db_path=tmp_path / "db", storage_dir=tmp_path / "f", allow_anonymous=True)
    app = create_app(settings, fake_factory())
    with TestClient(app) as client:
        assert client.get("/ready").json() == {"ready": True, "problems": []}
        worker = app.state.workers[0]
        assert worker.alive

    assert not worker.alive
