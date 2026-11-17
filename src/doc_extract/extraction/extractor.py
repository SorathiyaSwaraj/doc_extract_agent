"""Two baseline extraction methods, and a runner that saves predictions to disk.

- text: page text (from the text layer, or OCR for scans) goes to the LLM.
- vision: the rendered page image goes to the LLM, with no OCR at all.
- rules: regular expressions over the same page text, as a no-LLM baseline.

Predictions are saved as JSONL, one line per document, and scored separately. A
run can stop at any point (rate limits, a crash) and continue where it left off,
and scoring changes never require paying for new LLM calls.
"""

import io
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from doc_extract.extraction.llm import LLMError, LLMProvider
from doc_extract.extraction.prompts import PROMPT_VERSION, text_prompt, vision_prompt
from doc_extract.extraction.rules import RULES_VERSION, extract_with_rules
from doc_extract.extraction.schema import llm_schema
from doc_extract.ingestion.document import load_pdf, render_page
from doc_extract.ingestion.ocr import OcrEngine
from doc_extract.models import Invoice

Method = Literal["text", "vision", "rules"]
VISION_DPI = 150
MAX_PARSE_PASSES = 3


@dataclass
class Prediction:
    doc_id: str
    method: str
    prompt_version: str
    provider: str | None = None
    model: str | None = None
    raw: str | None = None
    invoice: dict | None = None
    error: str | None = None
    dropped_fields: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_seconds: float | None = None
    total_seconds: float | None = None


def parse_invoice(raw: str) -> tuple[Invoice | None, str | None, list[str]]:
    """Parse model output, setting unreadable fields to null rather than losing the invoice.

    Returns the invoice, an error if nothing usable came back, and the dropped fields.
    One amount the model copied as OCR garbage ("9870:46") should cost one field,
    not every field on the document.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, f"invalid JSON: {error}", []
    if not isinstance(data, dict):
        return None, "schema mismatch: top level is not an object", []
    dropped: list[str] = []
    for _ in range(MAX_PARSE_PASSES):
        try:
            return Invoice.model_validate(data), None, dropped
        except ValidationError as error:
            for problem in error.errors():
                path = problem["loc"]
                if not path or not _set_null(data, path):
                    return None, f"schema mismatch: {'.'.join(map(str, path))}", dropped
                dropped.append(".".join(map(str, path)))
    return None, "schema mismatch: too many invalid fields", dropped


def _set_null(data: object, path: tuple) -> bool:
    """Set the value at path to None. False if the path does not lead to a field."""
    *parents, last = path
    for key in parents:
        try:
            data = data[key]
        except (KeyError, IndexError, TypeError):
            return False
    if isinstance(data, dict) and last in data:
        data[last] = None
        return True
    return False


def extract(
    pdf: bytes,
    doc_id: str,
    method: Method,
    provider: LLMProvider | None,
    ocr: OcrEngine | None = None,
) -> Prediction:
    started = time.perf_counter()
    if method == "rules":
        prediction = Prediction(doc_id, method, RULES_VERSION, "rules", RULES_VERSION)
        text = load_pdf(pdf, ocr=ocr).text
        prediction.invoice = extract_with_rules(text).model_dump(mode="json")
        prediction.total_seconds = round(time.perf_counter() - started, 3)
        return prediction

    prediction = Prediction(doc_id, method, PROMPT_VERSION, provider.name, provider.model)
    try:
        if method == "text":
            document = load_pdf(pdf, ocr=ocr)
            if not document.text.strip():
                raise LLMError("no text found; pass an OCR engine for scanned documents")
            completion = provider.generate(text_prompt(document.text), llm_schema())
        else:
            image = io.BytesIO()
            render_page(pdf, 1, dpi=VISION_DPI).save(image, "PNG")
            completion = provider.generate(vision_prompt(), llm_schema(), [image.getvalue()])
    except LLMError as error:
        prediction.error = str(error)
    else:
        invoice, prediction.error, prediction.dropped_fields = parse_invoice(completion.text)
        prediction.raw = completion.text
        prediction.invoice = invoice.model_dump(mode="json") if invoice else None
        prediction.model = completion.model
        prediction.input_tokens = completion.input_tokens
        prediction.output_tokens = completion.output_tokens
        prediction.llm_seconds = round(completion.seconds, 3)
    prediction.total_seconds = round(time.perf_counter() - started, 3)
    return prediction


def run_extraction(
    data_dir: Path,
    split: str,
    method: Method,
    provider: LLMProvider | None,
    out_path: Path,
    limit: int | None = None,
    ocr: OcrEngine | None = None,
    on_prediction=None,
) -> int:
    """Append predictions to out_path, skipping documents already done. Returns new count."""
    records = [json.loads(line) for line in (data_dir / "manifest.jsonl").read_text().splitlines()]
    doc_ids = [record["doc_id"] for record in records if record["split"] == split][:limit]
    done = set()
    if out_path.exists():
        done = {json.loads(line)["doc_id"] for line in out_path.read_text().splitlines()}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("a") as out:
        for doc_id in doc_ids:
            if doc_id in done:
                continue
            pdf = (data_dir / f"{doc_id}.pdf").read_bytes()
            prediction = extract(pdf, doc_id, method, provider, ocr)
            out.write(json.dumps(asdict(prediction)) + "\n")
            out.flush()
            count += 1
            if on_prediction:
                on_prediction(prediction)
    return count


def reparse_file(path: Path) -> tuple[int, int]:
    """Re-parse saved raw model output with the current parser, without new LLM calls.

    Every run is then parsed the same way, whenever it was made. Returns the number
    of rows re-parsed and how many of them changed.
    """
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    parsed = changed = 0
    for row in rows:
        if row.get("raw") is None:
            continue
        invoice, error, dropped = parse_invoice(row["raw"])
        new = {
            "invoice": invoice.model_dump(mode="json") if invoice else None,
            "error": error,
            "dropped_fields": dropped,
        }
        parsed += 1
        changed += row.get("invoice") != new["invoice"] or row.get("error") != new["error"]
        row.update(new)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("".join(json.dumps(row) + "\n" for row in rows))
    temporary.replace(path)
    return parsed, changed
