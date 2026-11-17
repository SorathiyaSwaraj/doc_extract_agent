"""Extract, validate, and re-ask with the failed checks: fixed control flow.

The code decides every step; the model only ever produces an invoice. This is the
pipeline the tool-calling agent has to beat.

The danger in any repair loop is the model satisfying the checks instead of the
document: making totals add up by inventing a number. The prompt forbids it, and
evaluation measures it by counting fields that were right before repair and
wrong after.
"""

import io
import json
import time
from dataclasses import dataclass, field
from datetime import date

from doc_extract.extraction.extractor import Prediction, extract, parse_invoice
from doc_extract.extraction.llm import LLMError, LLMProvider
from doc_extract.extraction.prompts import RULES
from doc_extract.extraction.schema import llm_schema
from doc_extract.ingestion.document import load_pdf, render_page
from doc_extract.ingestion.ocr import OcrEngine
from doc_extract.models import Invoice
from doc_extract.validation.checks import Issue, validate
from doc_extract.validation.reference import VendorMaster, check_reference

REPAIR_VERSION = "repair-v1"
MAX_ROUNDS = 2
VISION_DPI = 150


@dataclass
class Round:
    issues: list[str]
    changed_fields: list[str] = field(default_factory=list)
    error: str | None = None


def find_issues(
    invoice: Invoice, vendors: VendorMaster | None, today: date | None = None
) -> list[Issue]:
    issues = validate(invoice, today) + check_reference(invoice, vendors)
    return [issue for issue in issues if issue.severity == "error"]


def repair_prompt(previous: Invoice, issues: list[Issue], hints: list[str], method: str) -> str:
    source = "the attached page image" if method == "vision" else "the document text below"
    lines = "\n".join(f"- {issue.message} (fields: {', '.join(issue.fields)})" for issue in issues)
    hint_block = "\n".join(f"- {hint}" for hint in hints)
    return (
        f"{RULES}\n\n"
        f"An earlier extraction of this invoice failed these checks:\n{lines}\n\n"
        + (f"Reference data, a hint only:\n{hint_block}\n\n" if hints else "")
        + "Re-read the flagged fields in "
        f"{source} and return the complete corrected JSON. Change a value only where the "
        "document shows something different. Never adjust a number to make a check pass: "
        "if the document itself is inconsistent, keep exactly what it shows.\n\n"
        f"Earlier extraction:\n{previous.model_dump_json()}"
    )


def vendor_hints(invoice: Invoice, issues: list[Issue], vendors: VendorMaster | None) -> list[str]:
    if vendors is None or not any(issue.code == "vendor_unknown" for issue in issues):
        return []
    hints = []
    for name in {invoice.seller.name, invoice.buyer.name} - {None}:
        for match in vendors.lookup(name=name):
            hints.append(f"vendor master lists {match['name']!r} with GSTIN {match['gstin']}")
    return hints or ["no vendor in the master resembles either party name"]


def changed_fields(before: Invoice, after: Invoice) -> list[str]:
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    return sorted(_diff(old, new, ""))


def _diff(old: object, new: object, path: str) -> set[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        return set().union(
            *(_diff(old.get(k), new.get(k), f"{path}.{k}".lstrip(".")) for k in old | new)
        )
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return set().union(
            set(),
            *(_diff(o, n, f"{path}[{i}]") for i, (o, n) in enumerate(zip(old, new, strict=True))),
        )
    return set() if old == new else {path}


def extract_with_repair(
    pdf: bytes,
    doc_id: str,
    method: str,
    provider: LLMProvider,
    ocr: OcrEngine | None = None,
    vendors: VendorMaster | None = None,
    max_rounds: int = MAX_ROUNDS,
    today: date | None = None,
) -> tuple[Prediction, list[Round]]:
    started = time.perf_counter()
    prediction = extract(pdf, doc_id, method, provider, ocr)
    prediction.method = f"repair-{method}"
    prediction.prompt_version = f"{prediction.prompt_version}+{REPAIR_VERSION}"
    rounds: list[Round] = []
    if prediction.invoice is None:
        return prediction, rounds

    current = Invoice.model_validate(prediction.invoice)
    source: tuple[str, list[bytes]] | None = None  # built only if a repair is needed
    for _ in range(max_rounds):
        issues = find_issues(current, vendors, today)
        if not issues:
            break
        record = Round([issue.code for issue in issues])
        rounds.append(record)
        source = source or _document_source(pdf, method, ocr)
        text, images = source
        prompt = repair_prompt(current, issues, vendor_hints(current, issues, vendors), method)
        if text:
            prompt += f"\n\n<document>\n{text}\n</document>"
        try:
            completion = provider.generate(prompt, llm_schema(), images)
        except LLMError as error:
            record.error = str(error)
            break
        _add_usage(prediction, completion)
        repaired, error, dropped = parse_invoice(completion.text)
        prediction.dropped_fields = dropped
        if repaired is None:
            record.error = error
            break
        record.changed_fields = changed_fields(current, repaired)
        current = repaired
    prediction.invoice = current.model_dump(mode="json")
    prediction.raw = json.dumps(prediction.invoice)
    prediction.total_seconds = round(time.perf_counter() - started, 3)
    return prediction, rounds


def _document_source(pdf: bytes, method: str, ocr: OcrEngine | None) -> tuple[str, list[bytes]]:
    if method == "vision":
        buffer = io.BytesIO()
        render_page(pdf, 1, dpi=VISION_DPI).save(buffer, "PNG")
        return "", [buffer.getvalue()]
    return load_pdf(pdf, ocr=ocr).text, []


def _add_usage(prediction: Prediction, completion) -> None:
    prediction.input_tokens = (prediction.input_tokens or 0) + (completion.input_tokens or 0)
    prediction.output_tokens = (prediction.output_tokens or 0) + (completion.output_tokens or 0)
    prediction.llm_seconds = round((prediction.llm_seconds or 0) + completion.seconds, 3)
