from datetime import date
from decimal import Decimal

import pytest

from doc_extract.extraction.llm import Completion, LLMError
from doc_extract.service.pipeline import PipelineConfig, TransientError, process_document
from doc_extract.validation.reference import DuplicateIndex

TODAY = date(2026, 11, 16)


class Replies:
    """Returns queued replies in order; the last one repeats."""

    name, model = "scripted", "m"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def generate(self, prompt, schema, images=()):
        self.calls += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return Completion(reply, self.name, self.model, 1000, 500, 0.5)


def with_total(invoice, total):
    totals = invoice.totals.model_copy(update={"grand_total": Decimal(total)})
    return invoice.model_copy(update={"totals": totals})


def test_two_agreeing_extractions_are_approved(sample_invoice):
    invoice, pdf = sample_invoice
    provider = Replies(invoice.model_dump_json())

    outcome = process_document(pdf, "d1", provider, None, PipelineConfig(), today=TODAY)

    assert outcome.decision == "approve"
    assert outcome.confidence == pytest.approx(0.98)
    assert [c.role for c in outcome.calls] == ["primary", "secondary"]
    assert [c.method for c in outcome.calls] == ["repair-vision", "text"]
    assert provider.calls == 2  # consistent first answer: no repair round
    assert set(outcome.stages) == {"primary", "secondary", "decide"}
    assert outcome.issues == []


def test_disagreement_goes_to_review_with_the_field(sample_invoice):
    invoice, pdf = sample_invoice
    # Vision reads a wrong total twice (repair cannot fix it); text reads it right.
    wrong = with_total(invoice, "1.00").model_dump_json()
    provider = Replies(wrong, wrong, wrong, invoice.model_dump_json())

    outcome = process_document(pdf, "d1", provider, None, PipelineConfig(), today=TODAY)

    assert outcome.decision == "review"
    assert outcome.fields_to_check[0]["field"] == "totals.grand_total"
    assert any(issue["code"] == "grand_total" for issue in outcome.issues)


def test_single_extraction_is_never_approved(sample_invoice):
    invoice, pdf = sample_invoice
    config = PipelineConfig(secondary=None, repair=False)

    outcome = process_document(pdf, "d1", Replies(invoice.model_dump_json()), None, config)

    assert outcome.decision == "review"
    assert outcome.confidence == pytest.approx(0.85)


def test_provider_outage_is_transient(sample_invoice):
    _, pdf = sample_invoice
    provider = Replies(LLMError("could not reach host"))

    with pytest.raises(TransientError, match="could not reach"):
        process_document(pdf, "d1", provider, None, PipelineConfig())


def test_unparseable_answer_goes_to_review_not_retry(sample_invoice):
    _, pdf = sample_invoice
    config = PipelineConfig(secondary=None, repair=False)

    outcome = process_document(pdf, "d1", Replies("not json"), None, config)

    assert outcome.decision == "review"
    assert outcome.invoice is None
    assert "invalid JSON" in outcome.reasons[0]


def test_failed_secondary_still_decides(sample_invoice):
    invoice, pdf = sample_invoice
    provider = Replies(invoice.model_dump_json(), LLMError("rate limited"))

    outcome = process_document(pdf, "d1", provider, None, PipelineConfig(), today=TODAY)

    assert outcome.decision == "review"
    assert outcome.calls[1].error == "rate limited"


def test_duplicate_of_an_approved_invoice_is_reviewed(sample_invoice):
    invoice, pdf = sample_invoice
    duplicates = DuplicateIndex()
    duplicates.add(invoice, "earlier")
    provider = Replies(invoice.model_dump_json())

    outcome = process_document(pdf, "d2", provider, None, PipelineConfig(), duplicates, today=TODAY)

    assert outcome.decision == "review"
    assert any("same seller and invoice number as earlier" in r for r in outcome.reasons)
