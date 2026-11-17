"""How well validation finds the documents that extraction got wrong.

Validation sees only the extracted invoice, never the gold label, so it can be
scored like any detector: of the documents with at least one wrong field, how
many did it flag (recall), and of the documents it flagged, how many were really
wrong (precision). Later phases route flagged documents to a human or a repair
step, so these two numbers decide how much review work and how many silent
errors remain.
"""

from collections import Counter
from datetime import date

from doc_extract.evaluation.report import Run
from doc_extract.models import Invoice
from doc_extract.validation.checks import has_errors, validate
from doc_extract.validation.reference import VendorMaster, check_reference


def validation_quality(
    run: Run, today: date | None = None, vendors: VendorMaster | None = None
) -> dict:
    caught = false_alarm = missed = clean = 0
    codes_on_wrong: Counter = Counter()
    for row, score in zip(run.rows, run.scores, strict=True):
        if row["invoice"] is None:
            flagged = True  # an unparsed extraction always goes to review
            codes: set[str] = {"unparsed"}
        else:
            invoice = Invoice.model_validate(row["invoice"])
            issues = validate(invoice, today) + check_reference(invoice, vendors)
            flagged = has_errors(issues)
            codes = {issue.code for issue in issues if issue.severity == "error"}
        wrong = not score.exact
        if wrong:
            codes_on_wrong.update(codes)
        caught += flagged and wrong
        false_alarm += flagged and not wrong
        missed += wrong and not flagged
        clean += not wrong and not flagged
    wrong_total, flagged_total = caught + missed, caught + false_alarm
    return {
        "documents": len(run.rows),
        "wrong": wrong_total,
        "flagged": flagged_total,
        "caught": caught,
        "missed": missed,
        "false_alarms": false_alarm,
        "recall": round(caught / wrong_total, 4) if wrong_total else None,
        "precision": round(caught / flagged_total, 4) if flagged_total else None,
        "codes_on_wrong_documents": dict(codes_on_wrong.most_common()),
    }
