import random
from decimal import Decimal

import pypdfium2 as pdfium
import pytest

from doc_extract.synthetic.invoices import generate_invoice
from doc_extract.synthetic.render import LAYOUTS, choose_style, format_amount, render_invoice


def pdf_text(data: bytes) -> list[str]:
    document = pdfium.PdfDocument(data)
    try:
        return [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()


@pytest.mark.parametrize(
    ("grouping", "expected"),
    [
        ("indian", "12,34,567.89"),
        ("western", "1,234,567.89"),
        ("none", "1234567.89"),
    ],
)
def test_format_amount_grouping(grouping, expected):
    assert format_amount(Decimal("1234567.89"), grouping) == expected


def test_format_amount_small_and_negative():
    assert format_amount(Decimal("999.5"), "indian") == "999.50"
    assert format_amount(Decimal("-0.40"), "indian") == "-0.40"
    assert format_amount(Decimal("100000"), "indian") == "1,00,000.00"


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("seed", range(5))
def test_printed_text_contains_gold_values(layout, seed):
    rng = random.Random(f"render-{seed}")
    invoice = generate_invoice(rng)
    style = choose_style(rng, layout)

    pages = pdf_text(render_invoice(invoice, style))
    text = " ".join(pages[0].split())

    assert len(pages) == 1
    assert invoice.invoice_number in text
    assert invoice.invoice_date.strftime(style.date_format) in text
    assert invoice.seller.gstin in text
    assert format_amount(invoice.totals.grand_total, style.grouping) in text
    assert style.labels["total"] in text
    for item in invoice.line_items:
        assert item.hsn_sac in text


def test_rendering_is_deterministic():
    invoice = generate_invoice(random.Random("same"))
    style = choose_style(random.Random("same"), "modern")

    assert render_invoice(invoice, style) == render_invoice(invoice, style)
