from doc_extract.service.pipeline import Outcome, TransientError
from doc_extract.service.store import Store
from doc_extract.service.worker import Worker


def done(pdf, doc_id):
    return Outcome(doc_id, "v", None, "review", 0.0, ["no extraction"], [], [])


def test_processes_and_stores_outcome(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-1", None)

    assert Worker(store, done).run_once() is True
    assert Worker(store, done).run_once() is False

    document = store.get(doc_id)
    assert document["status"] == "done" and document["decision"] == "review"
    assert document["result"]["reasons"] == ["no extraction"]


def test_transient_errors_retry_then_fail(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-1", None)

    def outage(pdf, doc_id):
        raise TransientError("HTTP 503")

    worker = Worker(store, outage, max_attempts=2, retry_base_seconds=0)
    worker.run_once()
    assert store.get(doc_id)["status"] == "queued"
    worker.run_once()

    document = store.get(doc_id)
    assert document["status"] == "failed" and document["attempts"] == 2
    assert "HTTP 503" in document["error"]


def test_unexpected_error_fails_without_retry(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    doc_id, _ = store.add(b"%PDF-1", None)

    def broken(pdf, doc_id):
        raise ValueError("bad page")

    Worker(store, broken).run_once()

    assert store.get(doc_id)["status"] == "failed"
    assert store.get(doc_id)["error"] == "ValueError: bad page"


def test_background_thread_drains_queue(tmp_path):
    store = Store(tmp_path / "db", tmp_path / "files")
    ids = [store.add(f"%PDF-{n}".encode(), None)[0] for n in range(3)]
    worker = Worker(store, done, poll_seconds=0.01)

    worker.start()
    for _ in range(500):
        if all(store.get(i)["status"] == "done" for i in ids):
            break
        worker._stop.wait(0.01)
    worker.stop()

    assert all(store.get(i)["status"] == "done" for i in ids)
