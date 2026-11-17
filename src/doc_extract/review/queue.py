"""Decide approve or review for each invoice, and tell the reviewer where to look.

A reviewer who rechecks every field of every invoice saves nothing. The queue lists
only the fields below the field threshold, weakest first, with the reasons, so
review time goes where errors are likely.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from doc_extract.models import Invoice
from doc_extract.review.confidence import calibrate, document_confidence, field_confidences
from doc_extract.validation.checks import has_errors, validate
from doc_extract.validation.reference import VendorMaster, check_reference

APPROVE_THRESHOLD = 0.98
FIELD_THRESHOLD = 0.95


@dataclass
class FieldToCheck:
    field: str
    value: object
    confidence: float
    reasons: list[str]


@dataclass
class ReviewItem:
    doc_id: str
    decision: str  # "approve" or "review"
    confidence: float
    reasons: list[str] = field(default_factory=list)
    fields_to_check: list[FieldToCheck] = field(default_factory=list)


def decide(
    doc_id: str,
    row: dict,
    other_row: dict | None = None,
    vendors: VendorMaster | None = None,
    calibration: dict[float, float] | None = None,
    approve_threshold: float = APPROVE_THRESHOLD,
    field_threshold: float = FIELD_THRESHOLD,
    today: date | None = None,
) -> ReviewItem:
    if row.get("invoice") is None:
        return ReviewItem(doc_id, "review", 0.0, [f"no extraction: {row.get('error')}"])
    invoice = Invoice.model_validate(row["invoice"])
    other = (
        Invoice.model_validate(other_row["invoice"])
        if other_row and other_row.get("invoice")
        else None
    )
    issues = validate(invoice, today) + check_reference(invoice, vendors)
    changed = {name for r in row.get("rounds", []) for name in r["changed_fields"]}
    confidences = field_confidences(
        invoice, other, issues, changed, set(row.get("dropped_fields") or []), vendors
    )
    if calibration:
        confidences = calibrate(confidences, calibration)
    weakest = document_confidence(confidences)
    reasons = [issue.message for issue in issues if issue.severity == "error"]
    if weakest < approve_threshold:
        reasons.append(f"weakest field confidence {weakest:.2f} is below {approve_threshold}")
    values = _flat_values(invoice)
    to_check = sorted(
        (
            FieldToCheck(name, values.get(name), round(c.confidence, 4), c.reasons)
            for name, c in confidences.items()
            if c.confidence < field_threshold
        ),
        key=lambda item: item.confidence,
    )
    decision = "review" if has_errors(issues) or weakest < approve_threshold else "approve"
    return ReviewItem(doc_id, decision, round(weakest, 4), reasons, to_check)


def build_queue(
    primary_path: Path,
    secondary_path: Path | None = None,
    vendors: VendorMaster | None = None,
    calibration: dict[float, float] | None = None,
    approve_threshold: float = APPROVE_THRESHOLD,
    today: date | None = None,
) -> list[ReviewItem]:
    """Review items first, weakest first; approvals after."""
    primary = _rows(primary_path)
    secondary = _rows(secondary_path) if secondary_path else {}
    items = [
        decide(
            doc_id,
            row,
            secondary.get(doc_id),
            vendors,
            calibration,
            approve_threshold,
            today=today,
        )
        for doc_id, row in primary.items()
    ]
    return sorted(items, key=lambda item: (item.decision == "approve", item.confidence))


def save_queue(items: list[ReviewItem], path: Path) -> None:
    path.write_text("".join(json.dumps(asdict(item), default=str) + "\n" for item in items))


def _rows(path: Path) -> dict[str, dict]:
    return {row["doc_id"]: row for row in map(json.loads, path.read_text().splitlines())}


def _flat_values(invoice: Invoice) -> dict[str, object]:
    data = invoice.model_dump(mode="json")
    values = {}
    for key, value in data.items():
        if isinstance(value, dict):
            values.update({f"{key}.{inner}": v for inner, v in value.items()})
        elif key == "line_items":
            for index, item in enumerate(value):
                values.update({f"line_items[{index}].{inner}": v for inner, v in item.items()})
        else:
            values[key] = value
    return values
