import pytest

from doc_extract.ingestion.document import load_pdf
from doc_extract.ingestion.ocr import RapidOcrEngine


def test_digital_pdf_uses_text_layer(sample_invoice, fake_ocr):
    invoice, pdf = sample_invoice

    document = load_pdf(pdf, ocr=fake_ocr)

    assert document.pages[0].source == "text_layer"
    assert invoice.invoice_number in document.text
    assert invoice.seller.gstin in document.text
    assert fake_ocr.images == []


def test_text_layer_boxes_read_top_down(sample_invoice):
    _, pdf = sample_invoice
    page = load_pdf(pdf).pages[0]

    tops = [line.box[1] for line in page.lines]
    assert all(0 <= top < page.height for top in tops)
    assert page.lines[0].text == "TAX INVOICE"


def test_image_only_page_without_ocr_returns_no_lines(image_only_pdf):
    page = load_pdf(image_only_pdf).pages[0]

    assert page.source == "ocr"
    assert page.lines == ()


def test_ocr_boxes_are_converted_to_points(image_only_pdf, fake_ocr):
    page = load_pdf(image_only_pdf, ocr=fake_ocr, dpi=200).pages[0]

    assert page.source == "ocr"
    assert page.lines[0].box == pytest.approx((72, 36, 144, 54))
    assert page.lines[0].score == 0.9


def test_force_ocr_skips_text_layer(sample_invoice, fake_ocr):
    _, pdf = sample_invoice

    page = load_pdf(pdf, ocr=fake_ocr, force_ocr=True).pages[0]

    assert page.source == "ocr"
    assert page.text == "Invoice No. A1"


def test_real_ocr_reads_a_scanned_invoice(sample_invoice, image_only_pdf):
    """Slow (a few seconds): runs the real OCR models on CPU."""
    invoice, _ = sample_invoice

    document = load_pdf(image_only_pdf, ocr=RapidOcrEngine())

    assert invoice.seller.gstin in document.text
