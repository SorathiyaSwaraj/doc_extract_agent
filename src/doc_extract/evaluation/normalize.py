"""How each field is compared with its gold value.

Identifiers are compared exactly, after removing only what cannot change their
meaning (spaces, and case for GSTINs). One wrong character in a GSTIN or invoice
number is a wrong value. Names, addresses, and descriptions allow small
differences, because a reviewer would accept them. Amounts compare as numbers,
so 18 equals 18.00.
"""

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

from doc_extract.gst import canonical_state

# Similarity (0 to 100) at which free text counts as the same value
FUZZY_THRESHOLD = 90

Comparator = Callable[[object, object], bool]


def same_identifier(predicted: object, gold: object) -> bool:
    return _squash(predicted) == _squash(gold)


def same_gstin(predicted: object, gold: object) -> bool:
    return _squash(predicted).upper() == _squash(gold).upper()


def same_text(predicted: object, gold: object) -> bool:
    return fuzz.ratio(_plain(predicted), _plain(gold)) >= FUZZY_THRESHOLD


def same_number(predicted: object, gold: object) -> bool:
    return Decimal(predicted) == Decimal(gold)


def same_date(predicted: object, gold: object) -> bool:
    return isinstance(predicted, date) and predicted == gold


def same_state(predicted: object, gold: object) -> bool:
    canonical = canonical_state(str(predicted))
    return canonical is not None and canonical == canonical_state(str(gold))


def same_currency(predicted: object, gold: object) -> bool:
    return str(predicted).strip().upper() == str(gold).strip().upper()


def _squash(value: object) -> str:
    return re.sub(r"\s", "", str(value))


def _plain(value: object) -> str:
    text = re.sub(r"[^\w&]+", " ", str(value).casefold())
    return " ".join(text.split())
