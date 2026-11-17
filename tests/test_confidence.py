import json
from datetime import date
from decimal import Decimal

import pytest

from doc_extract.evaluation.confidence_quality import confidence_quality
from doc_extract.review.confidence import (
    AGREED,
    CHANGED_IN_REPAIR,
    DISAGREED,
    FAILED_CHECK,
    SINGLE,
    VENDOR_CONFIRMED,
    calibrate,
    document_confidence,
    field_confidences,
    fit_calibration,
)
from doc_extract.synthetic.dataset import build_dataset
from doc_extract.validation.checks import validate
from doc_extract.validation.reference import VendorMaster

TODAY = date(2026, 11, 7)


@pytest.fixture
def invoice(sample_invoice):
    return sample_invoice[0]


def with_total(invoice, total):
    return invoice.model_copy(
        update={"totals": invoice.totals.model_copy(update={"grand_total": Decimal(total)})}
    )


def test_single_extraction_gets_the_base_level(invoice):
    confidences = field_confidences(invoice)

    assert {c.confidence for c in confidences.values()} == {SINGLE}
    assert "line_items[0].taxable_value" in confidences


def test_agreement_and_disagreement(invoice):
    other = with_total(invoice, "1.00")

    confidences = field_confidences(invoice, other)

    assert confidences["invoice_number"].confidence == AGREED
    assert confidences["totals.grand_total"].confidence == DISAGREED


def test_failed_check_caps_the_named_fields(invoice):
    wrong = with_total(invoice, "1.00")

    confidences = field_confidences(wrong, wrong, validate(wrong, TODAY))

    assert confidences["totals.grand_total"].confidence == FAILED_CHECK
    assert "failed grand_total" in confidences["totals.grand_total"].reasons
    assert confidences["invoice_number"].confidence == AGREED


def test_repair_changes_and_dropped_fields(invoice):
    confidences = field_confidences(
        invoice, invoice, changed={"totals.grand_total"}, dropped={"line_items.0.unit_price"}
    )

    assert confidences["totals.grand_total"].confidence == CHANGED_IN_REPAIR
    assert "line_items[0].unit_price" not in confidences


def test_vendor_master_confirms_seller_gstin(invoice):
    master = VendorMaster({invoice.seller.gstin: invoice.seller.name})

    confidences = field_confidences(invoice, vendors=master)

    assert confidences["seller.gstin"].confidence == VENDOR_CONFIRMED


def test_document_is_as_weak_as_its_weakest_field(invoice):
    confidences = field_confidences(invoice, with_total(invoice, "1.00"))

    assert document_confidence(confidences) == DISAGREED
    assert document_confidence({}) == 0


def test_calibration_replaces_levels_with_measured_accuracy(invoice):
    mapping = fit_calibration([{"confidence": SINGLE, "fields": 10, "accuracy": 0.7}])

    calibrated = calibrate(field_confidences(invoice), mapping)

    assert {c.confidence for c in calibrated.values()} == {0.7}


def test_confidence_quality_on_a_run(tmp_path):
    data = tmp_path / "data"
    build_dataset(data, count=6, seed="conf")
    ids = [
        json.loads(line)["doc_id"] for line in (data / "manifest.jsonl").read_text().splitlines()
    ]

    def run(path, damage):
        rows = []
        for doc_id in ids:
            invoice = json.loads((data / f"{doc_id}.json").read_text())
            rows.append({"doc_id": doc_id, "invoice": damage(doc_id, invoice)})
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def break_first(doc_id, invoice):
        if doc_id == ids[0]:
            invoice["invoice_number"] = "WRONG"
        return invoice

    primary = run(tmp_path / "primary.jsonl", lambda d, i: i)
    secondary = run(tmp_path / "secondary.jsonl", break_first)

    result = confidence_quality(primary, data, secondary, today=TODAY)

    levels = {item["confidence"]: item for item in result["reliability"]}
    assert levels[AGREED]["accuracy"] == 1
    assert levels[DISAGREED]["fields"] == 1  # the invoice number the second run got wrong
    strict = result["auto_approve"][0]
    assert strict["threshold"] == AGREED
    assert (strict["approved"], strict["approved_with_errors"]) == (5, 0)
    assert result["auto_approve"][-1]["approved"] == 6
    targeted = result["targeted_review"]
    assert targeted["errors"] == 0 and targeted["share_of_fields_to_check"] > 0
