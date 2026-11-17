"""Per-field confidence from signals that need no gold label.

Signals, strongest first:

- A second, independent extraction (text when the first was vision) that agrees.
  Two methods with different failure modes rarely make the same mistake.
- The vendor master holding the extracted seller GSTIN under a matching name.
- A failed validation check naming the field.
- The value changed during repair: re-reads are noisy, and a repair can bend a
  correct number to make a check pass.
- The parser dropped the value as unreadable.

The numbers below are starting points chosen by hand. They are not probabilities
until calibration (evaluation/confidence_quality.py) shows how often fields at
each level are really right.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from doc_extract.evaluation.matching import match_items
from doc_extract.evaluation.scoring import HEADER_FIELDS, ITEM_FIELDS, outcome
from doc_extract.models import Invoice, LineItem
from doc_extract.validation.checks import Issue
from doc_extract.validation.reference import NAME_MATCH, VendorMaster

SINGLE = 0.85
AGREED = 0.98
DISAGREED = 0.35
ONE_SIDE_MISSING = 0.5
VENDOR_CONFIRMED = 0.99
FAILED_CHECK = 0.3
CHANGED_IN_REPAIR = 0.5


@dataclass
class FieldConfidence:
    confidence: float
    reasons: list[str] = field(default_factory=list)

    def cap(self, limit: float, reason: str) -> None:
        if self.confidence > limit:
            self.confidence = limit
        self.reasons.append(reason)


def field_confidences(
    primary: Invoice,
    secondary: Invoice | None = None,
    issues: list[Issue] = (),
    changed: set[str] = frozenset(),
    dropped: set[str] = frozenset(),
    vendors: VendorMaster | None = None,
) -> dict[str, FieldConfidence]:
    """Confidence for every field the primary extraction filled, keyed like the scorer.

    Line-item fields are keyed `line_items[i].name` by the primary's row index.
    """
    result: dict[str, FieldConfidence] = {}
    for name, (get, same) in HEADER_FIELDS.items():
        if get(primary) is not None:
            other = get(secondary) if secondary is not None else None
            result[name] = _from_agreement(get(primary), other, secondary is not None, same)

    pairs = (
        dict(match_items(primary.line_items, secondary.line_items, _item_agreement))
        if secondary is not None
        else {}
    )
    for index, item in enumerate(primary.line_items):
        other_item = secondary.line_items[pairs[index]] if index in pairs else None
        for name, same in ITEM_FIELDS.items():
            value = getattr(item, name)
            if value is None:
                continue
            other = getattr(other_item, name) if other_item is not None else None
            result[f"line_items[{index}].{name}"] = _from_agreement(
                value, other, secondary is not None, same
            )

    seller = primary.seller
    known = vendors.vendors.get(seller.gstin) if vendors is not None else None
    if "seller.gstin" in result and known and _names_match(known, seller.name):
        result["seller.gstin"].confidence = VENDOR_CONFIRMED
        result["seller.gstin"].reasons.append("vendor master confirms")

    for issue in issues:
        if issue.severity != "error":
            continue
        for name in _expand(issue.fields, result):
            result[name].cap(FAILED_CHECK, f"failed {issue.code}")
    for name in _expand(changed, result):
        result[name].cap(CHANGED_IN_REPAIR, "changed during repair")
    for name in dropped:
        result.pop(_scorer_key(name), None)
    return result


def document_confidence(confidences: dict[str, FieldConfidence]) -> float:
    """A document is only as trustworthy as its weakest field."""
    return min((c.confidence for c in confidences.values()), default=0.0)


def _from_agreement(value, other, has_second: bool, same) -> FieldConfidence:
    if not has_second:
        return FieldConfidence(SINGLE, ["single extraction"])
    if other is None:
        return FieldConfidence(ONE_SIDE_MISSING, ["second extraction is empty"])
    if outcome(value, other, same) == "correct":
        return FieldConfidence(AGREED, ["both extractions agree"])
    return FieldConfidence(DISAGREED, ["extractions disagree"])


def _item_agreement(a: LineItem, b: LineItem) -> int:
    return sum(
        outcome(getattr(a, name), getattr(b, name), same) == "correct"
        for name, same in ITEM_FIELDS.items()
    )


def _names_match(known: str, name: str | None) -> bool:
    return bool(name) and fuzz.token_sort_ratio(known.casefold(), name.casefold()) >= NAME_MATCH


def _scorer_key(path: str) -> str:
    """'line_items.0.unit_price' or 'line_items[0].unit_price' -> 'line_items[0].unit_price'."""
    return re.sub(r"line_items\.(\d+)\.", r"line_items[\1].", path)


def _expand(names, result: dict[str, FieldConfidence]) -> list[str]:
    """Map field names from checks and diffs onto confidence keys.

    'line_items' alone means every line-item field; 'place_of_supply' and similar
    map to themselves when present.
    """
    keys = []
    for raw in names:
        name = _scorer_key(raw)
        if name == "line_items":
            keys += [key for key in result if key.startswith("line_items[")]
        elif name in result:
            keys.append(name)
    return keys


def fit_calibration(reliability: list[dict]) -> dict[float, float]:
    """Map each raw confidence level to the accuracy measured for it.

    Raw levels here are few and discrete, so histogram binning is exact: no curve
    to fit. Fit on one set of documents and apply to another, or the calibration
    error it reports is meaningless.
    """
    return {item["confidence"]: item["accuracy"] for item in reliability}


def calibrate(
    confidences: dict[str, FieldConfidence], mapping: dict[float, float]
) -> dict[str, FieldConfidence]:
    return {
        name: FieldConfidence(mapping.get(c.confidence, c.confidence), c.reasons)
        for name, c in confidences.items()
    }
