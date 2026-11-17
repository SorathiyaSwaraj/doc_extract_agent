import random
from datetime import date
from decimal import Decimal

import pytest

from doc_extract.models import Invoice
from doc_extract.synthetic.invoices import generate_invoice
from doc_extract.validation.checks import has_errors, validate

TODAY = date(2026, 11, 7)
GOLD = [generate_invoice(random.Random(f"checks-{n}")) for n in range(150)]


def codes(invoice):
    return {issue.code for issue in validate(invoice, TODAY)}


def change(invoice, **totals):
    return invoice.model_copy(update={"totals": invoice.totals.model_copy(update=totals)})


@pytest.mark.parametrize("invoice", GOLD)
def test_correct_invoices_raise_no_errors(invoice):
    """No false alarms: every generated invoice is right by construction."""
    assert not has_errors(validate(invoice, TODAY))


def test_empty_extraction_reports_missing_fields():
    assert codes(Invoice()) == {"missing"}
    assert len(validate(Invoice(), TODAY)) == 5


def test_misread_gstin_fails_checksum():
    invoice = GOLD[0]
    gstin = invoice.seller.gstin
    misread = gstin[:7] + ("8" if gstin[7] != "8" else "3") + gstin[8:]
    bad = invoice.model_copy(
        update={"seller": invoice.seller.model_copy(update={"gstin": misread})}
    )

    assert "gstin_invalid" in codes(bad)


def test_gstin_from_another_state_is_flagged():
    invoice = next(i for i in GOLD if i.seller.state != "Kerala")
    moved = invoice.seller.model_copy(update={"state": "Kerala"})

    assert "gstin_state_mismatch" in codes(invoice.model_copy(update={"seller": moved}))


def test_wrong_line_amount_breaks_line_and_sum_checks():
    invoice = GOLD[0]
    first = invoice.line_items[0]
    wrong = first.model_copy(update={"taxable_value": first.taxable_value + 100})
    bad = invoice.model_copy(update={"line_items": (wrong, *invoice.line_items[1:])})

    assert {"line_amount", "taxable_sum"} <= codes(bad)


def test_dropped_digit_in_grand_total_is_caught():
    invoice = GOLD[0]
    total = invoice.totals.grand_total

    assert "grand_total" in codes(change(invoice, grand_total=(total / 10).quantize(Decimal("1"))))


def test_wrong_tax_type_is_caught():
    intra = next(i for i in GOLD if i.totals.cgst is not None)
    as_igst = change(intra, igst=intra.totals.cgst * 2, cgst=None, sgst=None)

    assert "tax_type" in codes(as_igst)


def test_misread_tax_amount_is_caught():
    inter = next(i for i in GOLD if i.totals.igst is not None)

    assert "tax_amount" in codes(change(inter, igst=inter.totals.igst + 9))


def test_large_round_off_is_caught():
    assert "round_off" in codes(change(GOLD[0], round_off=Decimal("3.00")))


def test_unusual_rate_is_only_a_warning():
    invoice = GOLD[0]
    item = invoice.line_items[0].model_copy(update={"gst_rate": Decimal(7)})
    odd = invoice.model_copy(update={"line_items": (item, *invoice.line_items[1:])})

    issues = [i for i in validate(odd, TODAY) if i.code == "rate_unusual"]
    assert issues and all(issue.severity == "warning" for issue in issues)


@pytest.mark.parametrize(
    ("when", "code"), [(date(2027, 1, 1), "date_future"), (date(2016, 1, 1), "date_before_gst")]
)
def test_impossible_dates(when, code):
    assert code in codes(GOLD[0].model_copy(update={"invoice_date": when}))


def test_consistent_misread_passes_every_check():
    """The limit of validation: a wrong invoice number breaks no rule."""
    invoice = GOLD[0].model_copy(update={"invoice_number": "Tl/1045/2025-26"})

    assert not has_errors(validate(invoice, TODAY))
