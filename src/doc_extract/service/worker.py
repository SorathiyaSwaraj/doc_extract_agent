"""Background workers that take queued documents and run the pipeline on them.

Uploads return at once and extraction happens here, because one document takes
several model calls and OCR: 10 to 60 seconds, longer than any client should
hold a request open. The queue is the documents table itself (store.py), so
there is no broker to run and nothing is lost when the process restarts.

After each document the worker prices its model calls, records metrics, and logs
one line with the decision, timings, tokens, and cost. Before each document it
checks the daily budget, and waits instead of spending past it.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from doc_extract.observability.cost import Budget, PriceTable
from doc_extract.observability.logs import document_id
from doc_extract.observability.metrics import ServiceMetrics
from doc_extract.service.pipeline import Outcome, TransientError
from doc_extract.service.store import Store

log = logging.getLogger(__name__)

Process = Callable[[bytes, str], Outcome]


class Worker:
    def __init__(
        self,
        store: Store,
        process: Process,
        max_attempts: int = 3,
        poll_seconds: float = 1.0,
        retry_base_seconds: float = 30.0,
        name: str = "worker",
        prices: PriceTable | None = None,
        budget: Budget | None = None,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        self.store, self.process = store, process
        self.max_attempts, self.poll_seconds, self.name = max_attempts, poll_seconds, name
        self.retry_base_seconds = retry_base_seconds
        self.prices = prices or PriceTable()
        self.budget = budget or Budget(None)
        self.metrics = metrics
        self.paused_for_budget = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """Process one queued document. False if there was nothing to do."""
        if self.budget.exhausted(self.store.spent_since(start_of_day())):
            if not self.paused_for_budget:
                log.warning("daily budget of %s USD reached; pausing", self.budget.daily_usd)
            self.paused_for_budget = True
            return False
        self.paused_for_budget = False
        document = self.store.claim_next()
        if document is None:
            return False
        doc_id = document["id"]
        token = document_id.set(doc_id)  # every log line below carries the document id
        try:
            self._process(document)
        finally:
            document_id.reset(token)
        return True

    def _process(self, document: dict) -> None:
        doc_id = document["id"]
        try:
            outcome = self.process(self.store.pdf_path(doc_id).read_bytes(), doc_id)
        except TransientError as error:
            retry = document["attempts"] < self.max_attempts
            # Back off exponentially, so an outage is not met with a stream of calls.
            delay = self.retry_base_seconds * 2 ** (document["attempts"] - 1)
            log.warning(
                "transient failure: %s",
                error,
                extra={"attempt": document["attempts"], "retry": retry, "delay": delay},
            )
            self.store.fail(doc_id, f"transient: {error}", retry=retry, delay_seconds=delay)
            self._count_failure("transient")
        except Exception as error:  # a bug or a broken PDF: never retry blindly
            log.exception("processing failed")
            self.store.fail(doc_id, f"{type(error).__name__}: {error}", retry=False)
            self._count_failure("error")
        else:
            self.prices.price_outcome(outcome)
            self.store.complete(doc_id, outcome.to_dict())
            if self.metrics:
                self.metrics.observe_outcome(outcome)
            log.info(
                "decided %s",
                outcome.decision,
                extra={
                    "confidence": outcome.confidence,
                    "stages": outcome.stages,
                    "input_tokens": sum(c.input_tokens or 0 for c in outcome.calls),
                    "output_tokens": sum(c.output_tokens or 0 for c in outcome.calls),
                    "cost_usd": outcome.cost_usd,
                    "fields_to_check": len(outcome.fields_to_check),
                },
            )

    def _count_failure(self, kind: str) -> None:
        if self.metrics:
            self.metrics.failures.inc(kind=kind)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # the store itself failed; wait and try again
                log.exception("%s: could not claim a document", self.name)
                worked = False
            if not worked:
                self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self, timeout: float = 30) -> None:
        """Finish the current document, then stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)


def start_of_day() -> str:
    """Midnight UTC today, the start of the budget day, in the store's time format."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat(timespec="milliseconds")
