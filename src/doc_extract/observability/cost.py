"""What each document costs in model calls, and a daily budget that stops spending.

Prices are configuration, not code: they change, differ by tier and region, and
must be read from the provider's pricing page by whoever pays the bill. A model
without a configured price has an unknown cost (None), never a guessed one, so a
missing price shows up as a gap instead of a reassuring zero. Local models
(Ollama) cost nothing per token; their cost is the machine.

    DOC_EXTRACT_PRICES="gemini-3.5-flash-lite=0.10/0.40"   # USD per million in/out tokens
"""

from dataclasses import dataclass

PER_MILLION = 1_000_000
FREE_PROVIDERS = {"ollama", "rules"}


class PriceError(ValueError):
    pass


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float

    def cost(self, input_tokens: int | None, output_tokens: int | None) -> float:
        return (
            (input_tokens or 0) * self.input_per_million
            + (output_tokens or 0) * self.output_per_million
        ) / PER_MILLION


class PriceTable:
    def __init__(self, prices: dict[str, Price] | None = None) -> None:
        self.prices = prices or {}

    @classmethod
    def parse(cls, raw: str) -> "PriceTable":
        """'model=in/out,model=in/out', prices in USD per million tokens."""
        prices = {}
        for entry in filter(None, (part.strip() for part in raw.split(","))):
            model, sep, pair = entry.partition("=")
            low, slash, high = pair.partition("/")
            try:
                if not (sep and slash and model):
                    raise ValueError
                prices[model.strip()] = Price(float(low), float(high))
            except ValueError as error:
                raise PriceError(f"price entry {entry!r} is not model=input/output") from error
        return cls(prices)

    def lookup(self, provider: str | None, model: str | None) -> Price | None:
        if provider in FREE_PROVIDERS:
            return Price(0, 0)
        if not model:
            return None
        # Providers report versions such as gemini-x-001 for a configured gemini-x:
        # use the longest configured name that the reported model starts with.
        matches = [name for name in self.prices if model.startswith(name)]
        return self.prices[max(matches, key=len)] if matches else None

    def price_outcome(self, outcome) -> None:
        """Fill in the cost of each call, and the document total if every call is priced."""
        total: float | None = 0.0
        for call in outcome.calls:
            price = self.lookup(call.provider, call.model)
            call.cost_usd = (
                round(price.cost(call.input_tokens, call.output_tokens), 8) if price else None
            )
            total = None if total is None or call.cost_usd is None else total + call.cost_usd
        outcome.cost_usd = round(total, 8) if total is not None else None


@dataclass
class Budget:
    """Stop starting new documents once today's spend reaches the limit.

    The check runs before a document is claimed, because a document's cost is known
    only after its calls. Spend can therefore overshoot by at most one document per
    worker, which is bounded and small; refusing mid-document would waste the calls
    already made.
    """

    daily_usd: float | None

    def exhausted(self, spent_today: float) -> bool:
        return self.daily_usd is not None and spent_today >= self.daily_usd
