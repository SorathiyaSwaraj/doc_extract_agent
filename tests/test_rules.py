from datetime import date
from decimal import Decimal

from doc_extract.evaluation.report import load_run, summarise
from doc_extract.extraction.extractor import run_extraction
from doc_extract.extraction.rules import extract_with_rules
from doc_extract.synthetic.dataset import build_dataset

TEXT = """TAX INVOICE
Apex Steel & Co   Bill No.: INV/25-26/1913
State: Uttar Pradesh (09)   Dated: 15-Jul-2025
GSTIN: 09ABFCW6507J1ZW   Place of Supply: Uttar Pradesh (09)
Customer
GSTIN: Unregistered
Sub Total   2,37,082.84
CGST   22,177.42
SGST   22,177.42
Round Off   -0.68
Amount Payable   INR 2,81,437.00"""


def test_rules_read_labels_dates_and_amounts():
    invoice = extract_with_rules(TEXT)

    assert invoice.invoice_number == "INV/25-26/1913"
    assert invoice.invoice_date == date(2025, 7, 15)
    assert invoice.seller.name == "Apex Steel & Co"
    assert invoice.seller.gstin == "09ABFCW6507J1ZW"
    assert invoice.seller.state == "Uttar Pradesh"
    assert invoice.buyer.gstin is None
    assert invoice.place_of_supply == "Uttar Pradesh"
    assert invoice.totals.taxable_value == Decimal("237082.84")
    assert invoice.totals.round_off == Decimal("-0.68")
    assert invoice.totals.grand_total == Decimal("281437.00")
    assert invoice.totals.igst is None


def test_invoice_label_does_not_run_across_lines():
    """Found by the regression test below: the title line plus a seller called Nova."""
    text = "TAX INVOICE\nNova Textiles Private Limited   Invoice No.: GST-2026-2411"

    assert extract_with_rules(text).invoice_number == "GST-2026-2411"


def test_rules_leave_unknown_things_empty():
    invoice = extract_with_rules("nothing useful here")

    assert invoice.invoice_number is None and invoice.totals.grand_total is None


def test_rules_regression_on_generated_invoices(tmp_path):
    """End-to-end guard for ingestion, rules, and scoring, with no LLM or OCR involved.

    If a change to rendering, text layers, row rebuilding, or scoring breaks
    something, these header fields fall below 1.0 and this test fails in CI.
    """
    data = tmp_path / "data"
    build_dataset(data, count=40, seed="regression")
    predictions = tmp_path / "rules.jsonl"
    for split in ("train", "dev", "test", "test_layout"):
        run_extraction(data, split, "rules", None, predictions)

    fields = summarise(load_run(predictions, data))["fields"]

    for name in (
        "invoice_number",
        "invoice_date",
        "seller.name",
        "seller.gstin",
        "buyer.gstin",
        "seller.state",
        "place_of_supply",
        "totals.taxable_value",
        "totals.igst",
        "totals.round_off",
        "totals.grand_total",
    ):
        assert fields[name]["f1"] == 1.0, name
    assert fields["line_items.taxable_value"]["recall"] == 0
