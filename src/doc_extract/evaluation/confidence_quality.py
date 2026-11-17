"""Are confidence scores honest, and how much review can they save?

Reliability: group extracted fields by confidence and measure how often each
group is right. Scores are calibrated when fields at 0.98 are right about 98% of
the time.

Auto-approve curve: approve a document without review when it fails no check and
its weakest field is at or above a threshold. For each threshold, report the
share approved and the share of approved documents that contain an error. The
business picks the point: every approved error is a wrong payment or tax entry
nobody looked at.
"""

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from doc_extract.evaluation.matching import match_items
from doc_extract.evaluation.scoring import (
    HEADER_FIELDS,
    ITEM_FIELDS,
    _item_similarity,
    outcome,
    score_document,
)
from doc_extract.models import Invoice
from doc_extract.review.confidence import calibrate, document_confidence, field_confidences
from doc_extract.validation.checks import has_errors, validate
from doc_extract.validation.reference import VendorMaster, check_reference


def load_rows(path: Path) -> dict[str, dict]:
    return {row["doc_id"]: row for row in map(json.loads, path.read_text().splitlines())}


def confidence_quality(
    primary_path: Path,
    data_dir: Path,
    secondary_path: Path | None = None,
    vendors: VendorMaster | None = None,
    today: date | None = None,
    calibration: dict[float, float] | None = None,
    doc_ids: set[str] | None = None,
    field_threshold: float = 0.95,
) -> dict:
    """Pass `calibration` to score calibrated confidences; `doc_ids` to use a subset."""
    primary = load_rows(primary_path)
    if doc_ids is not None:
        primary = {doc_id: row for doc_id, row in primary.items() if doc_id in doc_ids}
    secondary = load_rows(secondary_path) if secondary_path else {}
    by_level: dict[float, list[bool]] = defaultdict(list)
    documents = []
    flagged_fields = flagged_errors = all_errors = 0
    for doc_id, row in primary.items():
        gold = Invoice.model_validate_json((data_dir / f"{doc_id}.json").read_text())
        if row["invoice"] is None:
            documents.append((0.0, True, False))  # nothing to approve: always reviewed
            continue
        predicted = Invoice.model_validate(row["invoice"])
        other_row = secondary.get(doc_id)
        other = (
            Invoice.model_validate(other_row["invoice"])
            if other_row and other_row["invoice"]
            else None
        )
        issues = validate(predicted, today) + check_reference(predicted, vendors)
        changed = {name for r in row.get("rounds", []) for name in r["changed_fields"]}
        confidences = field_confidences(
            predicted, other, issues, changed, set(row.get("dropped_fields") or []), vendors
        )
        if calibration is not None:
            confidences = calibrate(confidences, calibration)
        correct = _field_correctness(predicted, gold)
        for name, confidence in confidences.items():
            right = correct.get(name, False)
            by_level[confidence.confidence].append(right)
            flagged = confidence.confidence < field_threshold
            flagged_fields += flagged
            all_errors += not right
            flagged_errors += flagged and not right
        exact = score_document(doc_id, predicted, gold).exact
        documents.append((document_confidence(confidences), has_errors(issues), exact))

    reliability = [
        {"confidence": level, "fields": len(hits), "accuracy": round(sum(hits) / len(hits), 4)}
        for level, hits in sorted(by_level.items(), reverse=True)
    ]
    total_fields = sum(item["fields"] for item in reliability)
    calibration_error = sum(
        item["fields"] / total_fields * abs(item["confidence"] - item["accuracy"])
        for item in reliability
    )
    return {
        "documents": len(documents),
        "fields": total_fields,
        "reliability": reliability,
        "expected_calibration_error": round(calibration_error, 4),
        "auto_approve": _curve(documents),
        # If reviewers check only fields below the field threshold
        "targeted_review": {
            "field_threshold": field_threshold,
            "share_of_fields_to_check": round(flagged_fields / total_fields, 4)
            if total_fields
            else None,
            "share_of_errors_in_them": round(flagged_errors / all_errors, 4)
            if all_errors
            else None,
            "errors": all_errors,
        },
    }


def _field_correctness(predicted: Invoice, gold: Invoice) -> dict[str, bool]:
    correct = {
        name: outcome(get(predicted), get(gold), same) == "correct"
        for name, (get, same) in HEADER_FIELDS.items()
    }
    pairs = dict(match_items(predicted.line_items, gold.line_items, _item_similarity))
    for index, item in enumerate(predicted.line_items):
        gold_item = gold.line_items[pairs[index]] if index in pairs else None
        for name, same in ITEM_FIELDS.items():
            gold_value = getattr(gold_item, name) if gold_item is not None else None
            correct[f"line_items[{index}].{name}"] = (
                outcome(getattr(item, name), gold_value, same) == "correct"
            )
    return correct


def _curve(documents: list[tuple[float, bool, bool]]) -> list[dict]:
    thresholds = sorted({confidence for confidence, _, _ in documents}, reverse=True)
    curve = []
    for threshold in thresholds:
        approved = [
            exact
            for confidence, failed, exact in documents
            if not failed and confidence >= threshold
        ]
        if not approved and curve:
            continue
        wrong = sum(not exact for exact in approved)
        curve.append(
            {
                "threshold": threshold,
                "approved": len(approved),
                "approved_share": round(len(approved) / len(documents), 4),
                "approved_with_errors": wrong,
                "error_share_of_approved": round(wrong / len(approved), 4) if approved else None,
            }
        )
    return curve
