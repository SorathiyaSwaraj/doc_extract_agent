"""Pair predicted line items with gold line items before scoring their fields.

Comparing by position fails as soon as the model skips or adds one row: every
later row would be scored against the wrong gold row. Instead, choose the pairing
that maximises the total number of agreeing fields (the assignment problem).
Invoices have few rows, so an exact search over subsets of gold rows is fast.
"""

from collections.abc import Callable, Sequence
from functools import cache

# Above this many gold rows, fall back to greedy pairing (2^n states otherwise)
MAX_EXACT_ROWS = 14


def match_items[P, G](
    predicted: Sequence[P], gold: Sequence[G], similarity: Callable[[P, G], int]
) -> list[tuple[int, int]]:
    """Return (predicted index, gold index) pairs. Pairs with zero similarity are dropped."""
    scores = [[similarity(p, g) for g in gold] for p in predicted]
    if len(gold) > MAX_EXACT_ROWS:
        return _greedy(scores)

    @cache
    def best(index: int, used: int) -> tuple[int, tuple[tuple[int, int], ...]]:
        """Best total for predicted rows from `index` on, with gold rows in `used` taken."""
        if index == len(predicted):
            return 0, ()
        result = best(index + 1, used)  # leave this predicted row unmatched
        for gold_index, score in enumerate(scores[index]):
            if score > 0 and not used & (1 << gold_index):
                total, pairs = best(index + 1, used | (1 << gold_index))
                if total + score > result[0]:
                    result = (total + score, ((index, gold_index), *pairs))
        return result

    return list(best(0, 0)[1])


def _greedy(scores: list[list[int]]) -> list[tuple[int, int]]:
    candidates = sorted(
        ((score, p, g) for p, row in enumerate(scores) for g, score in enumerate(row) if score),
        reverse=True,
    )
    pairs, used_p, used_g = [], set(), set()
    for _, p, g in candidates:
        if p not in used_p and g not in used_g:
            pairs.append((p, g))
            used_p.add(p)
            used_g.add(g)
    return sorted(pairs)
