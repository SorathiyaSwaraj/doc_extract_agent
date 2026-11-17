from datetime import date
from decimal import Decimal

from doc_extract.agent.repair import changed_fields, extract_with_repair
from doc_extract.extraction.llm import Completion, LLMError
from doc_extract.models import Invoice
from doc_extract.validation.reference import VendorMaster

TODAY = date(2026, 11, 7)


class Replies:
    """Returns the queued replies in order and records each prompt."""

    name, model = "scripted", "m"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, prompt, schema, images=()):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Completion(reply, self.name, self.model, 100, 50, 1.0)


def with_total(invoice, total):
    return invoice.model_copy(
        update={"totals": invoice.totals.model_copy(update={"grand_total": Decimal(total)})}
    )


def test_consistent_extraction_makes_one_call(sample_invoice):
    invoice, pdf = sample_invoice
    provider = Replies(invoice.model_dump_json())

    prediction, rounds = extract_with_repair(pdf, "d", "text", provider, today=TODAY)

    assert rounds == [] and len(provider.prompts) == 1
    assert prediction.method == "repair-text"
    assert Invoice.model_validate(prediction.invoice) == invoice


def test_failed_check_is_sent_back_and_fixed(sample_invoice):
    invoice, pdf = sample_invoice
    wrong = with_total(invoice, "1.00")
    provider = Replies(wrong.model_dump_json(), invoice.model_dump_json())

    prediction, rounds = extract_with_repair(pdf, "d", "text", provider, today=TODAY)

    assert [r.issues for r in rounds] == [["grand_total"]]
    assert rounds[0].changed_fields == ["totals.grand_total"]
    repair = provider.prompts[1]
    assert "grand total is 1.00" in repair and "Never adjust a number" in repair
    assert invoice.seller.gstin in repair  # the document text is sent again
    assert Invoice.model_validate(prediction.invoice) == invoice
    assert (prediction.input_tokens, prediction.output_tokens) == (200, 100)


def test_stops_after_max_rounds(sample_invoice):
    invoice, pdf = sample_invoice
    wrong = with_total(invoice, "1.00").model_dump_json()
    provider = Replies(wrong, wrong, wrong)

    prediction, rounds = extract_with_repair(pdf, "d", "text", provider, max_rounds=2, today=TODAY)

    assert len(rounds) == 2 and len(provider.prompts) == 3
    assert all(r.changed_fields == [] for r in rounds)
    assert Invoice.model_validate(prediction.invoice).totals.grand_total == Decimal("1.00")


def test_provider_error_keeps_last_good_extraction(sample_invoice):
    invoice, pdf = sample_invoice
    wrong = with_total(invoice, "1.00")
    provider = Replies(wrong.model_dump_json(), LLMError("quota"))

    prediction, rounds = extract_with_repair(pdf, "d", "text", provider, today=TODAY)

    assert rounds[0].error == "quota"
    assert Invoice.model_validate(prediction.invoice) == wrong


def test_unknown_vendor_gets_master_hints(sample_invoice):
    invoice, pdf = sample_invoice
    master = VendorMaster({"29AAGCB7383J1Z4": invoice.seller.name})
    provider = Replies(invoice.model_dump_json(), invoice.model_dump_json())

    extract_with_repair(pdf, "d", "text", provider, vendors=master, max_rounds=1, today=TODAY)

    assert "vendor master lists" in provider.prompts[1]
    assert "29AAGCB7383J1Z4" in provider.prompts[1]


def test_vision_repair_resends_the_image(sample_invoice):
    invoice, pdf = sample_invoice
    images = []

    class Vision(Replies):
        def generate(self, prompt, schema, images_=()):
            images.append(list(images_))
            return super().generate(prompt, schema, images_)

    provider = Vision(with_total(invoice, "1.00").model_dump_json(), invoice.model_dump_json())

    extract_with_repair(pdf, "d", "vision", provider, today=TODAY)

    assert len(images) == 2 and images[1][0].startswith(b"\x89PNG")
    assert "<document>" not in provider.prompts[1]


def test_changed_fields_paths():
    before = Invoice(invoice_number="A", line_items=({"quantity": "1"},))
    after = Invoice(invoice_number="B", line_items=({"quantity": "2"},))

    assert changed_fields(before, after) == ["invoice_number", "line_items[0].quantity"]
