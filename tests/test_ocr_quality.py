import json
from decimal import Decimal

import pytest

from doc_extract.evaluation.ocr_quality import (
    character_error_rate,
    evaluate_ocr,
    find_key_values,
)
from doc_extract.ingestion.document import load_pdf
from doc_extract.ingestion.layout import Line
from doc_extract.models import Invoice, LineItem, Party, Totals
from doc_extract.synthetic.dataset import build_dataset


def test_cer_ignores_whitespace_differences():
    assert character_error_rate("Grand  Total\n100", "Grand Total 100") == 0


def test_cer_counts_character_edits():
    assert character_error_rate("GSTIN 0939", "GSTIN O939") == pytest.approx(1 / 10)


def test_key_values_ignore_spacing_and_digit_grouping():
    invoice = Invoice(
        invoice_number="INV/24-25/0042",
        seller=Party(gstin="27AAPFU0939F1ZV"),
        line_items=(LineItem(hsn_sac="7318", taxable_value=Decimal("281438")),),
        totals=Totals(taxable_value=Decimal("281438"), grand_total=Decimal("332097.00")),
    )
    text = "INV/24-25/ 0042  27AAPFU0939F1ZV  7318  2,81,438.00  3,32,O97.00"

    found = find_key_values(invoice, text)

    assert found["invoice_number"] == [True]
    assert found["gstin"] == [True]
    assert found["hsn_sac"] == [True]
    assert found["amount"] == [True, False, True]  # the misread O in the grand total


def test_evaluate_ocr_with_perfect_engine(tmp_path):
    """An engine that returns the text layer exactly scores zero error and full recall."""
    build_dataset(tmp_path, count=8, seed="t")
    first = json.loads((tmp_path / "manifest.jsonl").read_text().splitlines()[0])
    text = load_pdf(tmp_path / f"{first['doc_id']}.pdf").text

    def perfect_ocr(image):
        return [Line(text, (0, 0, image.width, 10))]

    report = evaluate_ocr(tmp_path, tmp_path, perfect_ocr, split=first["split"], limit=1)

    summary = report.summary()
    assert report.results[0].doc_id == first["doc_id"]
    assert summary["cer_mean"] == 0
    assert all(value == 1 for value in summary["recall"].values())
