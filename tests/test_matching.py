import itertools
import random

from doc_extract.evaluation.matching import _greedy, match_items


def agree(a, b):
    return sum(x == y for x, y in zip(a, b, strict=True))


GOLD = [("bolts", "7318", "10"), ("paper", "4802", "5"), ("wire", "8544", "2")]


def test_skipped_row_does_not_shift_later_rows():
    predicted = [GOLD[0], GOLD[2]]  # the model skipped the paper row

    assert match_items(predicted, GOLD, agree) == [(0, 0), (1, 2)]


def test_reordered_and_extra_rows():
    predicted = [GOLD[2], ("freight", "9965", "1"), GOLD[0]]

    assert sorted(match_items(predicted, GOLD, agree)) == [(0, 2), (2, 0)]


def test_unrelated_rows_stay_unmatched():
    assert match_items([("x", "y", "z")], GOLD, agree) == []


def test_exact_search_beats_greedy_when_greedy_is_trapped():
    # Greedy takes the single best pair (p0, g0) = 3, leaving p1 nothing: total 3.
    # The best pairing is (p0, g1) + (p1, g0) = 2 + 2 = 4.
    scores = {(0, 0): 3, (0, 1): 2, (1, 0): 2, (1, 1): 0}

    def similarity(p, g):
        return scores[(p, g)]

    exact = match_items([0, 1], [0, 1], similarity)

    assert sum(scores[pair] for pair in exact) == 4
    assert sum(scores[pair] for pair in _greedy([[3, 2], [2, 0]])) == 3


def test_exact_search_is_optimal_on_random_cases():
    rng = random.Random(1)
    for _ in range(50):
        n_pred, n_gold = rng.randint(0, 5), rng.randint(0, 5)
        scores = [[rng.randint(0, 4) for _ in range(n_gold)] for _ in range(n_pred)]

        pairs = match_items(range(n_pred), range(n_gold), lambda p, g, scores=scores: scores[p][g])

        best = 0
        for size in range(min(n_pred, n_gold) + 1):
            for ps in itertools.permutations(range(n_pred), size):
                for gs in itertools.combinations(range(n_gold), size):
                    best = max(best, sum(scores[p][g] for p, g in zip(ps, gs, strict=True)))
        assert sum(scores[p][g] for p, g in pairs) == best
