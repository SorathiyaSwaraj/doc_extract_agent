import random
from decimal import Decimal

import pytest

from doc_extract.gst import is_valid_gstin
from doc_extract.synthetic.invoices import (
    RATES_AFTER,
    RATES_BEFORE,
    SLAB_CHANGE,
    generate_invoice,
)

INVOICES = [generate_invoice(random.Random(f"test-{n}")) for n in range(200)]


def test_same_seed_gives_same_invoice():
    assert generate_invoice(random.Random("x")) == generate_invoice(random.Random("x"))


@pytest.mark.parametrize("invoice", INVOICES)
def test_totals_are_internally_consistent(invoice):
    totals = invoice.totals
    taxes = (totals.cgst or 0) + (totals.sgst or 0) + (totals.igst or 0)

    assert sum(i.taxable_value for i in invoice.line_items) == totals.taxable_value
    assert totals.taxable_value + taxes + totals.round_off == totals.grand_total
    assert abs(totals.round_off) <= Decimal("0.50")
    assert totals.grand_total == totals.grand_total.to_integral_value()


@pytest.mark.parametrize("invoice", INVOICES)
def test_tax_type_follows_place_of_supply(invoice):
    intra_state = invoice.seller.state == invoice.place_of_supply

    if intra_state:
        assert invoice.totals.igst is None
        assert invoice.totals.cgst == invoice.totals.sgst
    else:
        assert invoice.totals.cgst is None and invoice.totals.sgst is None
        assert invoice.totals.igst is not None


@pytest.mark.parametrize("invoice", INVOICES)
def test_rates_match_slabs_in_force(invoice):
    slabs = RATES_BEFORE if invoice.invoice_date < SLAB_CHANGE else RATES_AFTER

    assert all(item.gst_rate in slabs for item in invoice.line_items)


def test_gstins_are_valid_and_buyers_are_sometimes_unregistered():
    assert all(is_valid_gstin(invoice.seller.gstin) for invoice in INVOICES)
    buyer_gstins = [invoice.buyer.gstin for invoice in INVOICES]

    assert all(is_valid_gstin(gstin) for gstin in buyer_gstins if gstin)
    assert None in buyer_gstins


def test_both_tax_types_and_slab_periods_occur():
    assert any(invoice.totals.igst is not None for invoice in INVOICES)
    assert any(invoice.totals.cgst is not None for invoice in INVOICES)
    assert any(invoice.invoice_date < SLAB_CHANGE for invoice in INVOICES)
    assert any(invoice.invoice_date >= SLAB_CHANGE for invoice in INVOICES)
