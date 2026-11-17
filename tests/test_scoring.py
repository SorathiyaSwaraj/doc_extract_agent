import random
from decimal import Decimal

import pytest

from doc_extract.evaluation.normalize import same_number
from doc_extract.evaluation.scoring import (
    HEADER_FIELDS,
    ITEM_FIELDS,
    Counts,
    aggregate,
    outcome,
    score_document,
)
from doc_extract.models import Invoice
from doc_extract.synthetic.invoices import generate_invoice


@pytest.fixture
def gold():
    return generate_invoice(random.Random("scoring"))


def non_null_gold_values(invoice):
    header = sum(get(invoice) is not None for get, _ in HEADER_FIELDS.values())
    items = sum(
        getattr(item, name) is not None for item in invoice.line_items for name in ITEM_FIELDS
    )
    return header + items


@pytest.mark.parametrize(
    ("predicted", "gold", "expected"),
    [
        (Decimal(1), Decimal(1), "correct"),
        (None, Decimal(1), "missed"),
        (Decimal(2), Decimal(1), "wrong"),
        (Decimal(2), None, "hallucinated"),
        (None, None, "correct_null"),
        ("not a number", Decimal(1), "wrong"),
    ],
)
def test_outcomes(predicted, gold, expected):
    assert outcome(predicted, gold, same_number) == expected


def test_counts_metrics():
    counts = Counts(tp=8, fp=2, fn=4)

    assert counts.precision == 0.8
    assert counts.recall == pytest.approx(8 / 12)
    assert counts.f1 == pytest.approx(2 * 0.8 * (8 / 12) / (0.8 + 8 / 12))


def test_perfect_prediction_is_exact(gold):
    score = score_document("d", gold, gold)

    assert score.exact
    assert score.total.tp == non_null_gold_values(gold)
    assert score.total.fp == score.total.fn == 0


def test_failed_parse_misses_everything(gold):
    score = score_document("d", None, gold)

    assert not score.parsed and not score.exact
    assert score.total.tp == score.total.fp == 0
    assert score.total.fn == non_null_gold_values(gold)


def test_wrong_value_is_both_false_positive_and_false_negative(gold):
    wrong = gold.model_copy(update={"invoice_number": "WRONG"})

    counts = score_document("d", wrong, gold).fields["invoice_number"]

    assert (counts.tp, counts.fp, counts.fn) == (0, 1, 1)


def test_invented_tax_line_is_hallucinated(gold):
    if gold.totals.cess is not None:
        pytest.skip("generator never sets cess")
    invented = gold.model_copy(update={"totals": gold.totals.model_copy(update={"cess": 5})})

    score = score_document("d", invented, gold)

    assert score.outcomes["hallucinated"] == 1
    assert not score.exact


def test_skipped_line_item_costs_only_that_row(gold):
    if len(gold.line_items) < 2:
        gold = gold.model_copy(update={"line_items": gold.line_items * 2})
    skipped = gold.model_copy(update={"line_items": gold.line_items[1:]})

    items = {
        name: counts
        for name, counts in score_document("d", skipped, gold).fields.items()
        if name.startswith("line_items.")
    }

    assert all(counts.fp == 0 and counts.fn == 1 for counts in items.values())


def test_extra_line_item_is_hallucinated(gold):
    extra = gold.model_copy(update={"line_items": (*gold.line_items, gold.line_items[0])})

    score = score_document("d", extra, gold)

    assert score.outcomes["hallucinated"] == len(ITEM_FIELDS)


def test_formatting_differences_are_not_errors(gold):
    shouted = gold.model_copy(
        update={
            "seller": gold.seller.model_copy(
                update={"name": gold.seller.name.upper(), "gstin": gold.seller.gstin.lower()}
            ),
            "place_of_supply": f"{gold.place_of_supply} (99)",
        }
    )

    assert score_document("d", shouted, gold).exact


def test_aggregate_sums_documents(gold):
    scores = [score_document("a", gold, gold), score_document("b", None, gold)]

    result = aggregate(scores)

    assert (result.documents, result.parsed, result.exact) == (2, 1, 1)
    assert result.exact_rate == 0.5
    assert result.total.tp == non_null_gold_values(gold)
    assert result.total.recall == 0.5
    assert result.total.precision == 1.0


def test_empty_prediction_from_model_is_parsed_but_misses_everything(gold):
    score = score_document("d", Invoice(), gold)

    assert score.parsed
    assert score.total.tp == 1  # only currency, which defaults to INR
