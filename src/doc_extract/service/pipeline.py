"""The production path for one document: extract, check, score confidence, decide.

This joins the pieces that were built and measured separately in Phases 3 to 6,
in the configuration the measurements favoured: vision as the primary extraction
(it wins on scans and unseen layouts), repair against the failed checks, a text
extraction as an independent second opinion, and the approve-or-review policy.

Two kinds of failure are kept apart:

- The provider could not be reached or refused the call. That says nothing about
  the document, so the worker retries it later (TransientError).
- The model answered but the answer is poor. That is a fact about the document,
  so it goes to a human with reasons, never back into a retry loop.

A failed second extraction is not fatal either. The document is still decided,
on single-extraction confidence, which the policy never approves on its own.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date

from doc_extract.agent.repair import extract_with_repair
from doc_extract.agent.run import repair_row
from doc_extract.extraction.extractor import Prediction, extract
from doc_extract.extraction.llm import LLMProvider
from doc_extract.ingestion.ocr import OcrEngine
from doc_extract.models import Invoice
from doc_extract.review.queue import APPROVE_THRESHOLD, decide
from doc_extract.validation.checks import validate
from doc_extract.validation.reference import DuplicateIndex, VendorMaster, check_reference

PIPELINE_VERSION = "pipeline-v1"


class TransientError(RuntimeError):
    """The primary extraction failed for a reason outside the document. Retry later."""


@dataclass(frozen=True)
class PipelineConfig:
    primary: str = "vision"
    secondary: str | None = "text"
    repair: bool = True
    approve_threshold: float = APPROVE_THRESHOLD
    vendors: VendorMaster | None = None
    calibration: dict[float, float] | None = None


@dataclass
class Call:
    """One extraction made for a document, for cost and debugging."""

    role: str  # primary or secondary
    method: str
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    seconds: float | None
    error: str | None = None
    cost_usd: float | None = None  # filled in by the price table, if the model has a price


@dataclass
class Outcome:
    doc_id: str
    version: str
    invoice: dict | None
    decision: str  # approve or review
    confidence: float
    reasons: list[str]
    fields_to_check: list[dict]
    issues: list[dict]
    calls: list[Call] = field(default_factory=list)
    stages: dict[str, float] = field(default_factory=dict)  # stage -> seconds
    cost_usd: float | None = None  # all calls; None if any call has no known price

    def to_dict(self) -> dict:
        return asdict(self)


class Stages:
    """Wall time per pipeline stage."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}

    @contextmanager
    def time(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = round(time.perf_counter() - started, 3)


def process_document(
    pdf: bytes,
    doc_id: str,
    provider: LLMProvider,
    ocr: OcrEngine | None,
    config: PipelineConfig,
    duplicates: DuplicateIndex | None = None,
    today: date | None = None,
) -> Outcome:
    stages = Stages()

    with stages.time("primary"):
        if config.repair:
            prediction, rounds = extract_with_repair(
                pdf, doc_id, config.primary, provider, ocr, config.vendors, today=today
            )
            row = repair_row(prediction, rounds)
        else:
            prediction = extract(pdf, doc_id, config.primary, provider, ocr)
            row = asdict(prediction)
    if prediction.invoice is None and prediction.raw is None:
        # No answer at all: an outage, a rate limit, a bad key. Not the document's fault.
        raise TransientError(prediction.error or "primary extraction returned nothing")
    calls = [_call("primary", prediction)]

    other_row = None
    if config.secondary:
        with stages.time("secondary"):
            second = extract(pdf, doc_id, config.secondary, provider, ocr)
        calls.append(_call("secondary", second))
        other_row = asdict(second) if second.invoice is not None else None

    with stages.time("decide"):
        item = decide(
            doc_id,
            row,
            other_row,
            config.vendors,
            config.calibration,
            config.approve_threshold,
            today=today,
        )
        issues = []
        if prediction.invoice is not None:
            invoice = Invoice.model_validate(prediction.invoice)
            issues = validate(invoice, today) + check_reference(
                invoice, config.vendors, duplicates, doc_id
            )
            for issue in issues:
                if issue.code == "duplicate":
                    # decide() does not know the history of accepted invoices; this does.
                    item.decision = "review"
                    item.reasons.append(issue.message)

    return Outcome(
        doc_id=doc_id,
        version=PIPELINE_VERSION,
        invoice=prediction.invoice,
        decision=item.decision,
        confidence=item.confidence,
        reasons=item.reasons,
        fields_to_check=[asdict(f) for f in item.fields_to_check],
        issues=[asdict(issue) for issue in issues],
        calls=calls,
        stages=stages.seconds,
    )


def _call(role: str, prediction: Prediction) -> Call:
    return Call(
        role,
        prediction.method,
        prediction.provider,
        prediction.model,
        prediction.input_tokens,
        prediction.output_tokens,
        prediction.llm_seconds,
        prediction.error,
    )
