import io
import random

import pytest

from doc_extract.ingestion.document import render_page
from doc_extract.ingestion.layout import Line
from doc_extract.synthetic.invoices import generate_invoice
from doc_extract.synthetic.render import choose_style, render_invoice


class FakeOcr:
    """Returns fixed lines in pixel coordinates and records the images it saw."""

    def __init__(self, lines):
        self.lines = lines
        self.images = []

    def __call__(self, image):
        self.images.append(image)
        return list(self.lines)


@pytest.fixture
def fake_ocr():
    return FakeOcr([Line("Invoice No. A1", (200, 100, 400, 150), 0.9)])


@pytest.fixture(scope="session")
def sample_invoice():
    rng = random.Random("fixture")
    invoice = generate_invoice(rng)
    return invoice, render_invoice(invoice, choose_style(rng, "classic"))


@pytest.fixture(scope="session")
def image_only_pdf(sample_invoice):
    """The sample invoice as a picture, the way a scanner would deliver it."""
    _, pdf = sample_invoice
    buffer = io.BytesIO()
    render_page(pdf, 1, dpi=100).convert("L").save(buffer, "PDF", resolution=100)
    return buffer.getvalue()
