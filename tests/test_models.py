from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from doc_extract.models import Invoice, LineItem


def test_empty_invoice_is_valid():
    """A document the model could not read at all must still be storable."""
    invoice = Invoice()

    assert invoice.invoice_number is None
    assert invoice.line_items == ()
    assert invoice.currency == "INR"


def test_amounts_keep_exact_decimal_value():
    item = LineItem(unit_price="0.1", quantity="3")

    assert item.unit_price * item.quantity == Decimal("0.3")


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Rs. 2,81,438.00", "281438.00"),
        ("INR 1,034.15", "1034.15"),
        ("₹ 500", "500"),
        ("-0.43", "-0.43"),
        (1034.15, "1034.15"),
        ("1.072.40", "1072.40"),  # OCR read the comma as a point
        ("1.48.661.00", "148661.00"),
    ],
)
def test_amounts_are_read_as_printed(printed, expected):
    assert LineItem(taxable_value=printed).taxable_value == Decimal(expected)


def test_rates_accept_percent_sign():
    assert LineItem(gst_rate="18%").gst_rate == Decimal(18)


def test_unreadable_amount_is_rejected():
    with pytest.raises(ValidationError):
        LineItem(taxable_value="about five hundred")


def test_wrong_totals_are_stored_not_rejected():
    """Checking arithmetic is the validator's job, so extraction keeps what was printed."""
    invoice = Invoice.model_validate(
        {
            "line_items": [{"taxable_value": "100.00"}],
            "totals": {"taxable_value": "999.00", "grand_total": "5.00"},
        }
    )

    assert invoice.totals.grand_total == Decimal("5.00")


def test_round_trip_through_json():
    invoice = Invoice(
        invoice_number="INV/24-25/0042",
        invoice_date=date(2024, 7, 15),
        line_items=(LineItem(description="Steel bolts", taxable_value=Decimal("1250.50")),),
    )

    assert Invoice.model_validate_json(invoice.model_dump_json()) == invoice


def test_results_are_immutable():
    invoice = Invoice(invoice_number="A1")

    with pytest.raises(ValidationError):
        invoice.invoice_number = "B2"

    assert invoice.model_copy(update={"invoice_number": "B2"}).invoice_number == "B2"


def test_json_schema_carries_field_instructions():
    schema = Invoice.model_json_schema()

    assert "GSTIN" in schema["$defs"]["Party"]["properties"]["gstin"]["description"]
