"""Field-level scoring of extracted invoices against gold labels.

Each field of each document gets one outcome:

| Gold      | Prediction        | Outcome        | Counts as |
| --------- | ----------------- | -------------- | --------- |
| value     | same value        | correct        | TP        |
| value     | null              | missed         | FN        |
| value     | different value   | wrong          | FP and FN |
| null      | a value           | hallucinated   | FP        |
| null      | null              | correctly null | neither   |

Precision is how often a value the model gives is right; recall is how many gold
values the model gets right. A document is "exact" when no field is missed,
wrong, or hallucinated: such a document would need no human correction.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

from doc_extract.evaluation.matching import match_items
from doc_extract.evaluation.normalize import (
    Comparator,
    same_currency,
    same_date,
    same_gstin,
    same_identifier,
    same_number,
    same_state,
    same_text,
)
from doc_extract.models import Invoice, LineItem

Outcome = Literal["correct", "missed", "wrong", "hallucinated", "correct_null"]

HEADER_FIELDS: dict[str, tuple[Callable[[Invoice], object], Comparator]] = {
    "invoice_number": (lambda i: i.invoice_number, same_identifier),
    "invoice_date": (lambda i: i.invoice_date, same_date),
    "seller.name": (lambda i: i.seller.name, same_text),
    "seller.gstin": (lambda i: i.seller.gstin, same_gstin),
    "seller.address": (lambda i: i.seller.address, same_text),
    "seller.state": (lambda i: i.seller.state, same_state),
    "buyer.name": (lambda i: i.buyer.name, same_text),
    "buyer.gstin": (lambda i: i.buyer.gstin, same_gstin),
    "buyer.address": (lambda i: i.buyer.address, same_text),
    "buyer.state": (lambda i: i.buyer.state, same_state),
    "place_of_supply": (lambda i: i.place_of_supply, same_state),
    "totals.taxable_value": (lambda i: i.totals.taxable_value, same_number),
    "totals.cgst": (lambda i: i.totals.cgst, same_number),
    "totals.sgst": (lambda i: i.totals.sgst, same_number),
    "totals.igst": (lambda i: i.totals.igst, same_number),
    "totals.cess": (lambda i: i.totals.cess, same_number),
    "totals.round_off": (lambda i: i.totals.round_off, same_number),
    "totals.grand_total": (lambda i: i.totals.grand_total, same_number),
    "currency": (lambda i: i.currency, same_currency),
}

ITEM_FIELDS: dict[str, Comparator] = {
    "description": same_text,
    "hsn_sac": same_identifier,
    "quantity": same_number,
    "unit_price": same_number,
    "taxable_value": same_number,
    "gst_rate": same_number,
}


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: "Counts") -> None:
        self.tp, self.fp, self.fn = self.tp + other.tp, self.fp + other.fp, self.fn + other.fn

    def record(self, outcome: Outcome) -> None:
        self.tp += outcome == "correct"
        self.fp += outcome in ("wrong", "hallucinated")
        self.fn += outcome in ("wrong", "missed")

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class DocumentScore:
    doc_id: str
    parsed: bool
    fields: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    outcomes: Counter = field(default_factory=Counter)

    @cached_property
    def total(self) -> Counts:
        """Read only after scoring is finished: the result is cached."""
        total = Counts()
        for counts in self.fields.values():
            total.add(counts)
        return total

    @property
    def exact(self) -> bool:
        return self.parsed and self.total.fp == 0 and self.total.fn == 0


def outcome(predicted: object, gold: object, same: Comparator) -> Outcome:
    if gold is None:
        return "correct_null" if predicted is None else "hallucinated"
    if predicted is None:
        return "missed"
    try:
        return "correct" if same(predicted, gold) else "wrong"
    except (ArithmeticError, ValueError, TypeError):
        return "wrong"


def score_document(doc_id: str, predicted: Invoice | None, gold: Invoice) -> DocumentScore:
    """A failed parse is scored as an empty prediction: every gold value is missed."""
    score = DocumentScore(doc_id, parsed=predicted is not None)
    if predicted is None:
        # Skips validation, so currency can be empty like every other field.
        predicted = Invoice.model_construct(currency=None)

    def record(name: str, result: Outcome) -> None:
        score.fields[name].record(result)
        score.outcomes[result] += 1

    for name, (get, same) in HEADER_FIELDS.items():
        record(name, outcome(get(predicted), get(gold), same))

    pairs = match_items(predicted.line_items, gold.line_items, _item_similarity)
    matched_predicted = {p for p, _ in pairs}
    matched_gold = {g for _, g in pairs}
    for p, g in pairs:
        for name, same in ITEM_FIELDS.items():
            result = outcome(
                getattr(predicted.line_items[p], name), getattr(gold.line_items[g], name), same
            )
            record(f"line_items.{name}", result)
    for g, item in enumerate(gold.line_items):
        if g not in matched_gold:
            for name, same in ITEM_FIELDS.items():
                record(f"line_items.{name}", outcome(None, getattr(item, name), same))
    for p, item in enumerate(predicted.line_items):
        if p not in matched_predicted:
            for name, same in ITEM_FIELDS.items():
                record(f"line_items.{name}", outcome(getattr(item, name), None, same))
    return score


def _item_similarity(predicted: LineItem, gold: LineItem) -> int:
    return sum(
        outcome(getattr(predicted, name), getattr(gold, name), same) == "correct"
        for name, same in ITEM_FIELDS.items()
    )


@dataclass
class Aggregate:
    documents: int
    parsed: int
    exact: int
    fields: dict[str, Counts]
    total: Counts
    outcomes: Counter

    @property
    def exact_rate(self) -> float:
        return self.exact / self.documents if self.documents else 0.0


def aggregate(scores: list[DocumentScore]) -> Aggregate:
    fields: dict[str, Counts] = defaultdict(Counts)
    total, outcomes = Counts(), Counter()
    for score in scores:
        for name, counts in score.fields.items():
            fields[name].add(counts)
            total.add(counts)
        outcomes.update(score.outcomes)
    return Aggregate(
        len(scores),
        sum(score.parsed for score in scores),
        sum(score.exact for score in scores),
        dict(fields),
        total,
        outcomes,
    )
