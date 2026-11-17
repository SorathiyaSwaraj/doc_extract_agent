import json
from datetime import date
from decimal import Decimal

from doc_extract.review.queue import build_queue, decide, save_queue

TODAY = date(2026, 11, 7)


def row(invoice, **extra):
    return {"invoice": json.loads(invoice.model_dump_json()), **extra}


def test_agreeing_clean_invoice_is_approved(sample_invoice):
    invoice, _ = sample_invoice

    item = decide("d", row(invoice), row(invoice), today=TODAY)

    assert item.decision == "approve"
    assert item.fields_to_check == []


def test_failed_check_sends_to_review_with_fields(sample_invoice):
    invoice, _ = sample_invoice
    wrong = invoice.model_copy(
        update={"totals": invoice.totals.model_copy(update={"grand_total": Decimal("1.00")})}
    )

    item = decide("d", row(wrong), row(invoice), today=TODAY)

    assert item.decision == "review"
    assert any("grand total is 1.00" in reason for reason in item.reasons)
    first = item.fields_to_check[0]
    assert first.field == "totals.grand_total" and first.value == "1.00"
    assert "extractions disagree" in first.reasons


def test_single_extraction_is_reviewed_at_strict_threshold(sample_invoice):
    invoice, _ = sample_invoice

    assert decide("d", row(invoice), today=TODAY).decision == "review"
    assert decide("d", row(invoice), approve_threshold=0.8, today=TODAY).decision == "approve"


def test_missing_extraction_is_reviewed():
    item = decide("d", {"invoice": None, "error": "invalid JSON"}, today=TODAY)

    assert item.decision == "review" and "invalid JSON" in item.reasons[0]


def test_queue_puts_weakest_reviews_first(sample_invoice, tmp_path):
    invoice, _ = sample_invoice
    wrong = invoice.model_copy(update={"invoice_number": "OTHER"})
    primary, secondary = tmp_path / "p.jsonl", tmp_path / "s.jsonl"
    primary.write_text(
        "".join(json.dumps({"doc_id": d, **row(invoice)}) + "\n" for d in ("a", "b"))
    )
    secondary.write_text(
        json.dumps({"doc_id": "a", **row(invoice)})
        + "\n"
        + json.dumps({"doc_id": "b", **row(wrong)})
        + "\n"
    )

    queue = build_queue(primary, secondary, today=TODAY)
    save_queue(queue, tmp_path / "queue.jsonl")

    assert [(item.doc_id, item.decision) for item in queue] == [("b", "review"), ("a", "approve")]
    assert len((tmp_path / "queue.jsonl").read_text().splitlines()) == 2
