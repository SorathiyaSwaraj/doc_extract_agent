from datetime import date
from decimal import Decimal

import pytest

from doc_extract.evaluation.normalize import (
    canonical_state,
    same_date,
    same_gstin,
    same_identifier,
    same_number,
    same_state,
    same_text,
)


def test_identifiers_ignore_spaces_but_not_lookalike_letters():
    assert same_identifier("INV/24-25/ 0042", "INV/24-25/0042")
    assert not same_identifier("Tl/1045/2025-26", "TI/1045/2025-26")


def test_gstin_ignores_case_and_spaces():
    assert same_gstin("27aapfu0939f1zv", "27AAPFU0939F1ZV")
    assert same_gstin("27AAPFU 0939F1ZV", "27AAPFU0939F1ZV")
    assert not same_gstin("27AAPFU0939F1ZW", "27AAPFU0939F1ZV")


@pytest.mark.parametrize(
    ("predicted", "matches"),
    [
        ("Bharat Pharma Pvt Ltd", True),
        ("BHARAT PHARMA PVT. LTD.", True),
        ("Bharat Pherma Pvt Ltd", True),  # one OCR slip in 20 characters
        ("Bharat Agro Pvt Ltd", False),
    ],
)
def test_names_allow_small_differences(predicted, matches):
    assert same_text(predicted, "Bharat Pharma Pvt Ltd") is matches


def test_amounts_compare_as_numbers():
    assert same_number(Decimal("18"), Decimal("18.00"))
    assert not same_number(Decimal("-0.43"), Decimal("0.43"))


def test_dates_must_be_equal_dates():
    assert same_date(date(2025, 7, 5), date(2025, 7, 5))
    assert not same_date(date(2025, 5, 7), date(2025, 7, 5))  # day and month swapped


@pytest.mark.parametrize("printed", ["Maharashtra", "MAHARASHTRA", "27", "Maharashtra (27)", "7"])
def test_states_accept_names_and_codes(printed):
    expected = "Delhi" if printed == "7" else "Maharashtra"
    assert canonical_state(printed) == expected


def test_unknown_state_never_matches():
    assert not same_state("Atlantis", "Atlantis")
