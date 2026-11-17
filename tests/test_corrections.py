import json
from decimal import Decimal

import pytest

from doc_extract.service.corrections import CorrectionError, apply_corrections


def data(invoice):
    return json.loads(invoice.model_dump_json())


def test_header_and_line_item_corrections(sample_invoice):
    invoice, _ = sample_invoice

    corrected, changes = apply_corrections(
        data(invoice),
        {"seller.gstin": "27AAPFU0939F1ZV", "line_items[0].quantity": "3", "invoice_number": "X1"},
    )

    assert corrected.seller.gstin == "27AAPFU0939F1ZV"
    assert corrected.line_items[0].quantity == Decimal("3")
    assert changes["invoice_number"] == {"before": invoice.invoice_number, "after": "X1"}


def test_printed_amounts_are_parsed_like_extractions(sample_invoice):
    invoice, _ = sample_invoice

    corrected, _ = apply_corrections(data(invoice), {"totals.grand_total": "Rs. 1,234.50"})

    assert corrected.totals.grand_total == Decimal("1234.50")


@pytest.mark.parametrize(
    "path",
    ["seller.phone", "line_items[99].quantity", "totals", "line_items[0]", "seller..gstin", ""],
)
def test_unknown_or_group_fields_are_refused(sample_invoice, path):
    invoice, _ = sample_invoice

    with pytest.raises(CorrectionError):
        apply_corrections(data(invoice), {path: "1"})


def test_invalid_value_is_refused(sample_invoice):
    invoice, _ = sample_invoice

    with pytest.raises(CorrectionError, match="totals.grand_total"):
        apply_corrections(data(invoice), {"totals.grand_total": "abc"})


def test_missing_extraction_can_be_filled_in():
    corrected, changes = apply_corrections(None, {"invoice_number": "A-1"})

    assert corrected.invoice_number == "A-1"
    assert changes["invoice_number"]["before"] is None
