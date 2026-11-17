import pytest

from doc_extract.observability.cost import Budget, Price, PriceError, PriceTable
from doc_extract.service.pipeline import Call, Outcome


def call(provider="gemini", model="gemini-x-001", tokens=(1_000_000, 500_000)):
    return Call("primary", "vision", provider, model, *tokens, 1.0)


def test_parse_and_price_per_million_tokens():
    table = PriceTable.parse("gemini-x=0.10/0.40, other=1/2")

    assert table.prices["gemini-x"] == Price(0.10, 0.40)
    assert table.lookup("gemini", "gemini-x").cost(1_000_000, 500_000) == pytest.approx(0.30)


def test_reported_version_matches_longest_configured_prefix():
    table = PriceTable.parse("gemini=9/9,gemini-x=0.10/0.40")

    assert table.lookup("gemini", "gemini-x-001") == Price(0.10, 0.40)


def test_unknown_model_has_unknown_cost_not_zero():
    outcome = Outcome("d", "v", None, "review", 0.3, [], [], [])
    outcome.calls = [call(), call(model="mystery")]

    PriceTable.parse("gemini-x=0.10/0.40").price_outcome(outcome)

    assert outcome.calls[0].cost_usd == pytest.approx(0.30)
    assert outcome.calls[1].cost_usd is None
    assert outcome.cost_usd is None


def test_local_models_are_free():
    outcome = Outcome("d", "v", None, "review", 0.3, [], [], [])
    outcome.calls = [call(provider="ollama", model="qwen2.5vl:3b")]

    PriceTable().price_outcome(outcome)

    assert outcome.cost_usd == 0


@pytest.mark.parametrize("raw", ["gemini", "gemini=0.1", "=1/2", "g=a/b"])
def test_malformed_prices_are_refused(raw):
    with pytest.raises(PriceError):
        PriceTable.parse(raw)


def test_budget():
    assert not Budget(None).exhausted(1e9)
    assert not Budget(5.0).exhausted(4.99)
    assert Budget(5.0).exhausted(5.0)
