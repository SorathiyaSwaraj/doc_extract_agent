from decimal import Decimal

import pytest

from doc_extract.gst import gstin_check_char, is_valid_gstin, round_paise

# Widely published sample GSTINs
PUBLISHED = ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4"]


@pytest.mark.parametrize("gstin", PUBLISHED)
def test_published_gstins_are_valid(gstin):
    assert gstin_check_char(gstin[:14]) == gstin[14]
    assert is_valid_gstin(gstin)


@pytest.mark.parametrize(
    "gstin",
    [
        "27AAPFU0939F1ZW",  # wrong checksum
        "27AAPFU9039F1ZV",  # adjacent digits swapped
        "99AAPFU0939F1ZV",  # unknown state
        "27AAPFU0939F1Z",  # too short
        "27aapfu0939f1zv",  # lower case
        "27AAPF10939F1ZV",  # PAN shape broken
    ],
)
def test_invalid_gstins_are_rejected(gstin):
    assert not is_valid_gstin(gstin)


def test_checksum_misses_some_multi_character_errors():
    """A valid checksum is evidence, not proof: this OCR-style misread still passes."""
    assert is_valid_gstin("27AAPFU0393F1ZV")


def test_round_paise_rounds_half_up():
    assert round_paise(Decimal("2.345")) == Decimal("2.35")
    assert round_paise(Decimal("2.335")) == Decimal("2.34")
