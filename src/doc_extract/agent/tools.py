"""The agent's tools. All of them read; none can change anything outside the run.

That is the security boundary from ADR 0001: a document that says "ignore your
instructions" can at worst corrupt its own extraction, which validation and human
review exist to catch. No tool sends, pays, approves, or writes.
"""

import io
from datetime import date
from typing import Any

from pydantic import ValidationError

from doc_extract.extraction.schema import llm_schema
from doc_extract.ingestion.document import load_pdf, render_page
from doc_extract.ingestion.ocr import OcrEngine
from doc_extract.models import Invoice
from doc_extract.validation.checks import validate
from doc_extract.validation.reference import VendorMaster, check_reference

ZOOM_DPI = 200
# Vertical slices of the page; they overlap so no row is cut in half
PARTS = {"top": (0.0, 0.40), "middle": (0.30, 0.75), "bottom": (0.60, 1.0), "full": (0.0, 1.0)}


def declarations() -> list[dict[str, Any]]:
    invoice = {"type": "object", "properties": {"invoice": llm_schema()}, "required": ["invoice"]}
    return [
        {
            "name": "read_text",
            "description": "The page text, rebuilt row by row. From OCR if the page is a scan, "
            "so it may contain misread characters.",
            "parametersJsonSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "view_page",
            "description": "An image of part of the page. A smaller part shows more detail: "
            "top has the parties and invoice details, middle the line items, bottom the totals.",
            "parametersJsonSchema": {
                "type": "object",
                "properties": {"part": {"type": "string", "enum": list(PARTS)}},
                "required": ["part"],
            },
        },
        {
            "name": "validate",
            "description": "Check a draft for arithmetic, tax, GSTIN, date, and vendor-master "
            "problems. Returns the problems found; an empty list means none.",
            "parametersJsonSchema": invoice,
        },
        {
            "name": "lookup_vendor",
            "description": "Search the vendor master by GSTIN or by name.",
            "parametersJsonSchema": {
                "type": "object",
                "properties": {"gstin": {"type": "string"}, "name": {"type": "string"}},
            },
        },
        {
            "name": "submit",
            "description": "Submit the final invoice. This ends the task.",
            "parametersJsonSchema": invoice,
        },
    ]


class Toolbox:
    def __init__(
        self,
        pdf: bytes,
        ocr: OcrEngine | None = None,
        vendors: VendorMaster | None = None,
        today: date | None = None,
    ) -> None:
        self.pdf, self.ocr, self.vendors, self.today = pdf, ocr, vendors, today
        self._text: str | None = None

    def run(self, name: str, args: dict) -> tuple[dict, list[bytes]]:
        """Return the tool's result and any images to show the model."""
        if name == "read_text":
            if self._text is None:
                self._text = load_pdf(self.pdf, ocr=self.ocr).text
            return {"text": self._text}, []
        if name == "view_page":
            part = args.get("part", "full")
            if part not in PARTS:
                return {"error": f"part must be one of {list(PARTS)}"}, []
            return {"result": f"image of the {part} of the page attached"}, [self._crop(part)]
        if name == "validate":
            invoice, error = parse_tool_invoice(args)
            if invoice is None:
                return {"error": error}, []
            issues = validate(invoice, self.today) + check_reference(invoice, self.vendors)
            return {
                "problems": [
                    {"severity": i.severity, "fields": list(i.fields), "message": i.message}
                    for i in issues
                ]
            }, []
        if name == "lookup_vendor":
            if self.vendors is None:
                return {"error": "no vendor master is available"}, []
            return {"matches": self.vendors.lookup(args.get("gstin"), args.get("name"))}, []
        return {"error": f"unknown tool {name}"}, []

    def _crop(self, part: str) -> bytes:
        top, bottom = PARTS[part]
        image = render_page(self.pdf, 1, dpi=ZOOM_DPI)
        width, height = image.size
        buffer = io.BytesIO()
        image.crop((0, int(top * height), width, int(bottom * height))).save(buffer, "PNG")
        return buffer.getvalue()


def parse_tool_invoice(args: dict) -> tuple[Invoice | None, str | None]:
    try:
        return Invoice.model_validate(args.get("invoice", {})), None
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:5]
        )
        return None, f"invoice does not match the schema: {problems}"
