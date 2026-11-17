"""How much of each document survives OCR.

Two measures. Character error rate compares OCR text with the clean PDF's text
layer. Key-value recall asks whether the values extraction will need (invoice
number, GSTINs, HSN codes, amounts) appear in the OCR text at all. A value
missing here cannot be extracted later from text, which makes recall a ceiling
for the OCR-plus-LLM method.
"""

import json
import re
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from doc_extract.ingestion.document import load_pdf
from doc_extract.ingestion.ocr import OcrEngine
from doc_extract.models import Invoice


@dataclass
class DocResult:
    doc_id: str
    cer: float
    seconds: float
    found: dict[str, list[bool]]


@dataclass
class OcrReport:
    results: list[DocResult] = field(default_factory=list)

    def summary(self) -> dict:
        found_by_kind = defaultdict(list)
        for result in self.results:
            for kind, hits in result.found.items():
                found_by_kind[kind].extend(hits)
        cers = [result.cer for result in self.results]
        return {
            "documents": len(self.results),
            "cer_mean": round(statistics.fmean(cers), 4),
            "cer_median": round(statistics.median(cers), 4),
            "cer_max": round(max(cers), 4),
            "recall": {k: round(sum(v) / len(v), 4) for k, v in sorted(found_by_kind.items())},
            "seconds_per_page": round(statistics.fmean(r.seconds for r in self.results), 2),
            "worst": [r.doc_id for r in sorted(self.results, key=lambda r: -r.cer)[:3]],
        }


def normalise(text: str) -> str:
    return " ".join(text.split())


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference, hypothesis = normalise(reference), normalise(hypothesis)
    return Levenshtein.distance(reference, hypothesis) / max(len(reference), 1)


def key_values(invoice: Invoice) -> dict[str, list[str]]:
    """Values as they must appear in text, ignoring spaces and digit grouping."""
    gstins = [gstin for gstin in (invoice.seller.gstin, invoice.buyer.gstin) if gstin]
    amounts = [invoice.totals.taxable_value, invoice.totals.grand_total]
    amounts += [item.taxable_value for item in invoice.line_items]
    return {
        "invoice_number": [invoice.invoice_number],
        "gstin": gstins,
        "hsn_sac": [item.hsn_sac for item in invoice.line_items],
        "amount": [f"{amount:.2f}" for amount in amounts],
    }


def find_key_values(invoice: Invoice, text: str) -> dict[str, list[bool]]:
    compact = _compact(text)
    return {
        kind: [_compact(value) in compact for value in values]
        for kind, values in key_values(invoice).items()
    }


def evaluate_ocr(
    data_dir: Path,
    reference_dir: Path,
    ocr: OcrEngine,
    split: str,
    limit: int | None = None,
) -> OcrReport:
    records = [json.loads(line) for line in (data_dir / "manifest.jsonl").read_text().splitlines()]
    records = [record for record in records if record["split"] == split][:limit]
    report = OcrReport()
    for record in records:
        doc_id = record["doc_id"]
        invoice = Invoice.model_validate_json((data_dir / f"{doc_id}.json").read_text())
        reference = load_pdf(reference_dir / f"{doc_id}.pdf").text
        started = time.perf_counter()
        document = load_pdf(data_dir / f"{doc_id}.pdf", ocr=ocr, force_ocr=True)
        seconds = time.perf_counter() - started
        cer = character_error_rate(reference, document.text)
        found = find_key_values(invoice, document.text)
        report.results.append(DocResult(doc_id, cer, seconds / len(document.pages), found))
    return report


def _compact(text: str) -> str:
    return re.sub(r"[\s,]", "", text)
