import json
import threading

from doc_extract.service.store import Store


def outcome(decision="review", invoice=None, confidence=0.3):
    return {
        "decision": decision,
        "confidence": confidence,
        "invoice": invoice,
        "reasons": ["weakest field"],
        "fields_to_check": [{"field": "seller.gstin"}],
    }


def test_same_bytes_are_stored_once(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")

    first, created = store.add(b"%PDF-1 a", "a.pdf")
    again, created_again = store.add(b"%PDF-1 a", "copy.pdf")

    assert created and not created_again and first == again
    assert store.pdf_path(first).read_bytes() == b"%PDF-1 a"


def test_claim_takes_oldest_and_never_twice(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")
    ids = [store.add(f"%PDF-{n}".encode(), None)[0] for n in range(20)]
    claimed, lock = [], threading.Lock()

    def take():
        while (document := store.claim_next()) is not None:
            with lock:
                claimed.append(document["id"])

    threads = [threading.Thread(target=take) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(claimed) == sorted(ids)  # each exactly once
    assert store.claim_next() is None


def test_crash_leaves_document_to_requeue(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-x", None)
    store.claim_next()

    restarted = Store(tmp_path / "db.sqlite", tmp_path / "files")

    assert restarted.requeue_stale() == 1
    assert restarted.claim_next()["attempts"] == 2


def test_retry_waits_for_backoff(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-x", None)
    store.claim_next()

    store.fail(doc_id, "transient: 503", retry=True, delay_seconds=60)

    assert store.get(doc_id)["status"] == "queued"
    assert store.claim_next() is None  # not before the delay


def test_review_queue_and_review_history(tmp_path, sample_invoice):
    invoice, _ = sample_invoice
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")
    weak, _ = store.add(b"%PDF-weak", None)
    strong, _ = store.add(b"%PDF-strong", None)
    auto, _ = store.add(b"%PDF-auto", None)
    data = json.loads(invoice.model_dump_json())
    for doc_id, result in (
        (weak, outcome(confidence=0.3, invoice=data)),
        (strong, outcome(confidence=0.9, invoice=data)),
        (auto, outcome("approve", data, 0.99)),
    ):
        store.claim_next()
        store.complete(doc_id, result)

    assert [item["id"] for item in store.review_queue()] == [weak, strong]
    assert store.get(auto)["final"] == "approved"

    changes = {"seller.gstin": {"before": "A", "after": "B"}}
    store.add_review(weak, "alice", "approve", changes, data, None)

    document = store.get(weak)
    assert document["final"] == "approved"
    assert document["reviews"][0]["reviewer"] == "alice"
    assert document["reviews"][0]["corrections"] == changes
    assert [item["id"] for item in store.review_queue()] == [strong]


def test_duplicate_index_holds_approved_invoices_only(tmp_path, sample_invoice):
    invoice, _ = sample_invoice
    store = Store(tmp_path / "db.sqlite", tmp_path / "files")
    reviewed, _ = store.add(b"%PDF-1", None)
    store.claim_next()
    store.complete(reviewed, outcome(invoice=json.loads(invoice.model_dump_json())))

    assert store.duplicate_index().seen == {}

    store.add_review(reviewed, "bob", "approve", {}, json.loads(invoice.model_dump_json()), None)

    assert list(store.duplicate_index().seen.values()) == [reviewed]
