import json

import pytest

from doc_extract.evaluation.report import load_run, summarise
from doc_extract.extraction.extractor import extract, parse_invoice, run_extraction
from doc_extract.extraction.llm import Completion, LLMError
from doc_extract.models import Invoice
from doc_extract.synthetic.dataset import build_dataset


class ScriptedProvider:
    """Answers each call with reply(prompt, images) and records what it was sent."""

    name, model = "scripted", "test-model"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate(self, prompt, schema, images=()):
        self.calls.append((prompt, schema, list(images)))
        return Completion(self.reply(prompt, images), self.name, self.model, 1000, 200, 0.5)


@pytest.fixture
def dataset(tmp_path):
    build_dataset(tmp_path, count=12, seed="t")
    return tmp_path


def gold_json(dataset, doc_id):
    return (dataset / f"{doc_id}.json").read_text()


def test_text_method_sends_document_text(sample_invoice):
    invoice, pdf = sample_invoice
    provider = ScriptedProvider(lambda prompt, images: invoice.model_dump_json())

    prediction = extract(pdf, "d1", "text", provider)

    prompt, schema, images = provider.calls[0]
    assert invoice.seller.gstin in prompt
    assert "<document>" in prompt
    assert images == []
    assert "line_items" in schema["properties"]
    assert prediction.error is None
    assert Invoice.model_validate(prediction.invoice) == invoice
    assert (prediction.input_tokens, prediction.output_tokens) == (1000, 200)


def test_vision_method_sends_only_a_png(sample_invoice):
    invoice, pdf = sample_invoice
    provider = ScriptedProvider(lambda prompt, images: invoice.model_dump_json())

    extract(pdf, "d1", "vision", provider)

    prompt, _, images = provider.calls[0]
    assert invoice.seller.gstin not in prompt
    assert len(images) == 1 and images[0].startswith(b"\x89PNG")


def test_scanned_pdf_without_ocr_fails_without_calling_llm(image_only_pdf):
    provider = ScriptedProvider(lambda prompt, images: "{}")

    prediction = extract(image_only_pdf, "d1", "text", provider)

    assert provider.calls == []
    assert "OCR" in prediction.error


def test_invalid_json_is_recorded_not_raised():
    invoice, error, dropped = parse_invoice("not json")

    assert invoice is None and error.startswith("invalid JSON") and dropped == []


def test_unreadable_fields_are_dropped_not_the_whole_invoice(sample_invoice):
    invoice, _ = sample_invoice
    data = json.loads(invoice.model_dump_json())
    data["totals"]["sgst"] = "9870:46"
    data["line_items"][0]["unit_price"] = "31320L 1P"

    parsed, error, dropped = parse_invoice(json.dumps(data))

    assert error is None
    assert sorted(dropped) == ["line_items.0.unit_price", "totals.sgst"]
    assert parsed.totals.sgst is None and parsed.line_items[0].unit_price is None
    assert parsed.invoice_number == invoice.invoice_number


def test_non_object_output_is_an_error():
    assert parse_invoice("[1, 2]")[1].startswith("schema mismatch")


def test_provider_errors_are_recorded(sample_invoice):
    _, pdf = sample_invoice

    class Failing:
        name, model = "failing", "m"

        def generate(self, prompt, schema, images=()):
            raise LLMError("quota exhausted")

    prediction = extract(pdf, "d1", "text", Failing())

    assert prediction.error == "quota exhausted"
    assert prediction.invoice is None


def test_run_resumes_and_scores(dataset, tmp_path):
    def answer(prompt, images):
        # Find which document this is by its invoice number, then return the gold label.
        for path in dataset.glob("*.json"):
            gold = Invoice.model_validate_json(path.read_text())
            if gold.invoice_number in prompt and gold.seller.gstin in prompt:
                return path.read_text()
        raise AssertionError("unknown document")

    out = tmp_path / "predictions.jsonl"
    provider = ScriptedProvider(answer)

    first = run_extraction(dataset, "train", "text", provider, out, limit=2)
    second = run_extraction(dataset, "train", "text", provider, out, limit=3)

    assert (first, second) == (2, 1)
    summary = summarise(load_run(out, dataset))
    assert summary["documents"] == 3
    assert summary["parsed"] == 1
    assert summary["exact_rate"] == 1
    assert summary["models"] == ["scripted/test-model"]


def test_rules_method_needs_no_provider(sample_invoice):
    invoice, pdf = sample_invoice

    prediction = extract(pdf, "d1", "rules", None)

    predicted = Invoice.model_validate(prediction.invoice)
    assert (prediction.provider, prediction.input_tokens) == ("rules", None)
    assert predicted.invoice_number == invoice.invoice_number
    assert predicted.totals.grand_total == invoice.totals.grand_total
