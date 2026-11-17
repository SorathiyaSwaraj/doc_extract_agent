"""Turn a PDF into positioned text lines, from its text layer or from OCR.

Both sources produce the same shape: lines with boxes in PDF points, measured
from the top-left corner. Later stages never need to know which source was used,
but the page records it, because OCR text deserves less trust.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium
from PIL import Image

from doc_extract.ingestion.layout import Line, lines_to_text
from doc_extract.ingestion.ocr import OcrEngine

POINTS_PER_INCH = 72
# Fewer visible characters than this means the page is an image, possibly with a stray
# header or page number in its text layer.
MIN_TEXT_LAYER_CHARS = 30


@dataclass(frozen=True)
class Page:
    number: int
    width: float
    height: float
    source: Literal["text_layer", "ocr"]
    lines: tuple[Line, ...]

    @property
    def text(self) -> str:
        return lines_to_text(self.lines)


@dataclass(frozen=True)
class Document:
    pages: tuple[Page, ...]

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages)


def load_pdf(
    source: Path | bytes, ocr: OcrEngine | None = None, force_ocr: bool = False, dpi: int = 200
) -> Document:
    """Read each page's text layer, and OCR pages that have none.

    Without an OCR engine, image-only pages come back with no lines, so callers
    can see the gap instead of getting an error.
    """
    pdf = pdfium.PdfDocument(source)
    try:
        pages = []
        for index, page in enumerate(pdf):
            width, height = page.get_size()
            lines = () if force_ocr else _text_layer_lines(page, height)
            source_kind = "text_layer"
            if sum(len(line.text.strip()) for line in lines) < MIN_TEXT_LAYER_CHARS:
                source_kind = "ocr"
                lines = _ocr_lines(page, ocr, dpi) if ocr else ()
            pages.append(Page(index + 1, width, height, source_kind, tuple(lines)))
        return Document(tuple(pages))
    finally:
        pdf.close()


def render_page(source: Path | bytes, page_number: int, dpi: int = 200) -> Image.Image:
    """Page image for OCR or a vision model. Page numbers start at 1."""
    pdf = pdfium.PdfDocument(source)
    try:
        return _render(pdf[page_number - 1], dpi)
    finally:
        pdf.close()


def _render(page: pdfium.PdfPage, dpi: int) -> Image.Image:
    # For some pixel formats to_pil() shares memory with pdfium's bitmap. Copy, so the
    # image owns its pixels whatever the format and outlives the document.
    return page.render(scale=dpi / POINTS_PER_INCH).to_pil().copy()


def _text_layer_lines(page: pdfium.PdfPage, page_height: float) -> list[Line]:
    text_page = page.get_textpage()
    lines = []
    for index in range(text_page.count_rects()):
        left, bottom, right, top = text_page.get_rect(index)
        text = text_page.get_text_bounded(left, bottom, right, top).strip()
        if text:
            # PDF y grows upwards from the bottom; flip it so all boxes read top-down.
            lines.append(Line(text, (left, page_height - top, right, page_height - bottom)))
    return lines


def _ocr_lines(page: pdfium.PdfPage, ocr: OcrEngine, dpi: int) -> list[Line]:
    image = _render(page, dpi)
    to_points = POINTS_PER_INCH / dpi
    return [
        Line(line.text, tuple(v * to_points for v in line.box), line.score, line.angle)
        for line in ocr(image)
    ]
