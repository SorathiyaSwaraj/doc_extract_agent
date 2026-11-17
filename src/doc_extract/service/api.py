"""HTTP API: upload invoices, read results, work the review queue.

    POST /v1/documents                 upload a PDF; 202 with an id, processed in the background
    GET  /v1/documents                 list documents, filtered by status or decision
    GET  /v1/documents/{id}            result, issues, fields to check, review history
    GET  /v1/documents/{id}/pdf        the original file, for the reviewer
    GET  /v1/review-queue              documents waiting for a person, least confident first
    POST /v1/documents/{id}/review     approve or reject, with corrections
    GET  /v1/stats                     automation, correction rate, cost, latency
    GET  /metrics                      Prometheus metrics
    GET  /health, /ready               liveness and readiness, without a key

The service never pays, posts, or approves anything outside its own database
(ADR 0001). "Approved" here means "safe to hand to the next system", and that
hand-off is someone else's decision.

Uploads are untrusted input: size is capped before the body is read in full, the
bytes must open as a PDF within a page limit, and nothing in them is ever run.
"""

import hmac
import logging
import re
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import pypdfium2 as pdfium
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from doc_extract.observability.cost import Budget
from doc_extract.observability.logs import configure_logging, request_id
from doc_extract.observability.metrics import ServiceMetrics
from doc_extract.service.config import Settings
from doc_extract.service.corrections import CorrectionError, apply_corrections
from doc_extract.service.store import Store
from doc_extract.service.worker import Process, Worker, start_of_day
from doc_extract.validation.checks import has_errors, validate

log = logging.getLogger(__name__)

ProcessFactory = Callable[[Settings, Store], Process]
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    corrections: dict[str, Any] = Field(
        default_factory=dict,
        description="Field -> corrected value, keyed like fields_to_check, "
        "for example {'seller.gstin': '27AAPFU0939F1ZV'}.",
    )
    note: str | None = Field(None, max_length=2000)


def create_app(
    settings: Settings,
    process_factory: ProcessFactory | None = None,
    start_workers: bool = True,
) -> FastAPI:
    """Build the app. Tests pass a fake process factory and drive the worker by hand."""
    store = Store(settings.db_path, settings.storage_dir)
    factory = process_factory or default_process_factory
    workers: list[Worker] = []
    startup_problems: list[str] = []
    metrics = ServiceMetrics()
    metrics.registry.gauge(
        "doc_extract_documents",
        "Documents by status.",
        lambda: {(status,): count for status, count in store.status_counts().items()},
        ("status",),
    )
    metrics.registry.gauge(
        "doc_extract_budget_paused",
        "1 while workers wait because the daily budget is spent.",
        lambda: {(): float(any(w.paused_for_budget for w in workers))},
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        requeued = store.requeue_stale()
        if requeued:
            log.warning("requeued %d documents left processing by a previous run", requeued)
        if start_workers:
            try:
                for number in range(settings.workers):
                    worker = Worker(
                        store,
                        factory(settings, store),
                        settings.max_attempts,
                        name=f"w{number}",
                        prices=settings.prices,
                        budget=Budget(settings.daily_budget_usd),
                        metrics=metrics,
                    )
                    worker.start()
                    workers.append(worker)
            except Exception as error:
                # Stay up, so uploads are accepted and /ready says what is wrong. Documents
                # wait in the queue until a restart with a working configuration.
                log.error("workers not started: %s", error)
                startup_problems.append(f"workers not started: {error}")
        yield
        for worker in workers:
            worker.stop()

    app = FastAPI(
        title="Document Extraction Agent",
        version="0.1.0",
        lifespan=lifespan,
        description="Extracts, validates, and routes GST invoices for review.",
    )
    app.state.store = store
    app.state.settings = settings
    app.state.workers = workers
    app.state.metrics = metrics

    @app.middleware("http")
    async def observe(request: Request, call_next):
        """Request id, one log line, and metrics for every request."""
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            seconds = time.perf_counter() - started
            # The route template (/v1/documents/{doc_id}), never the raw path: one series
            # per endpoint, not one per document.
            route = getattr(request.scope.get("route"), "path", "unmatched")
            metrics.requests.inc(method=request.method, route=route, status=str(status))
            metrics.request_seconds.observe(seconds, route=route)
            if route not in ("/health", "/ready", "/metrics"):
                log.info(
                    "%s %s %d",
                    request.method,
                    route,
                    status,
                    extra={"seconds": round(seconds, 4)},
                )
            request_id.reset(token)

    def principal(
        x_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        """The name behind the key, from X-API-Key or `Authorization: Bearer <key>`."""
        if settings.allow_anonymous and not settings.api_keys:
            return "anonymous"
        if not x_api_key and authorization and authorization.lower().startswith("bearer "):
            x_api_key = authorization[7:].strip()
        if x_api_key:
            # Compare against every key in constant time, so timing reveals nothing.
            for key, name in settings.api_keys.items():
                if hmac.compare_digest(key.encode(), x_api_key.encode()):
                    return name
        raise HTTPException(401, "missing or invalid X-API-Key")

    User = Annotated[str, Depends(principal)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        problems = list(startup_problems)
        try:
            store.ping()
        except Exception as error:  # report, do not crash the probe
            problems.append(f"database: {error}")
        if start_workers and not any(worker.alive for worker in workers):
            problems.append("no worker is running")
        status = 503 if problems else 200
        return JSONResponse({"ready": not problems, "problems": problems}, status_code=status)

    @app.post("/v1/documents", status_code=202)
    def upload(user: User, file: Annotated[UploadFile, File()]) -> JSONResponse:
        # A plain def runs in a thread pool, so PDF parsing and disk writes never block
        # the event loop. Read one byte past the limit to know if the file is too big.
        pdf = file.file.read(settings.max_upload_bytes + 1)
        if len(pdf) > settings.max_upload_bytes:
            raise HTTPException(413, f"file is larger than {settings.max_upload_bytes} bytes")
        check_pdf(pdf, settings.max_pages)
        doc_id, created = store.add(pdf, _safe_name(file.filename))
        metrics.uploads.inc(new=str(created).lower())
        log.info("upload %s by %s (new=%s)", doc_id, user, created)
        document = store.get(doc_id)
        body = {"id": doc_id, "status": document["status"], "duplicate_upload": not created}
        return JSONResponse(body, status_code=202 if created else 200)

    @app.get("/v1/documents")
    def list_documents(
        user: User,
        status: Literal["queued", "processing", "done", "failed"] | None = None,
        decision: Literal["approve", "review"] | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict]:
        return store.documents(status, decision, limit, offset)

    @app.get("/v1/documents/{doc_id}")
    def get_document(user: User, doc_id: str) -> dict:
        return _document_or_404(store, doc_id)

    @app.get("/v1/documents/{doc_id}/pdf")
    def get_pdf(user: User, doc_id: str) -> Response:
        _document_or_404(store, doc_id)
        return Response(
            store.pdf_path(doc_id).read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/v1/review-queue")
    def review_queue(user: User, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict]:
        return store.review_queue(limit)

    @app.post("/v1/documents/{doc_id}/review")
    def review(user: User, doc_id: str, request: ReviewRequest) -> dict:
        document = _document_or_404(store, doc_id)
        if document["status"] != "done":
            raise HTTPException(409, f"document is {document['status']}, not ready for review")
        try:
            corrected, changes = apply_corrections(document["invoice"], request.corrections)
        except CorrectionError as error:
            raise HTTPException(422, str(error)) from error
        remaining = validate(corrected)
        if request.action == "approve" and has_errors(remaining) and not request.note:
            # Approving an invoice that still breaks a rule is allowed, but never silently.
            raise HTTPException(
                409,
                {
                    "message": "the corrected invoice still fails checks; fix it, or approve "
                    "with a note explaining why",
                    "issues": [i.message for i in remaining if i.severity == "error"],
                },
            )
        store.add_review(
            doc_id,
            user,
            request.action,
            changes,
            corrected.model_dump(mode="json"),
            request.note,
        )
        metrics.reviews.inc(action=request.action)
        metrics.corrections.inc(len(changes))
        log.info("review %s by %s: %s, %d corrections", doc_id, user, request.action, len(changes))
        return store.get(doc_id)

    @app.get("/v1/stats")
    def stats(user: User) -> dict:
        result = store.stats(since=start_of_day())
        result["daily_budget_usd"] = settings.daily_budget_usd
        result["paused_for_budget"] = any(worker.paused_for_budget for worker in workers)
        return result

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus(user: User) -> PlainTextResponse:
        # Counts and timings only, but still behind a key: the volume of a company's
        # invoices is business information. Prometheus sends the key as a header.
        return PlainTextResponse(metrics.registry.render(), media_type="text/plain; version=0.0.4")

    return app


def app_from_env() -> FastAPI:
    """Entry point for uvicorn: `uvicorn --factory doc_extract.service.api:app_from_env`."""
    settings = Settings.from_env()
    configure_logging(settings.log_level, settings.log_json)
    return create_app(settings)


def check_pdf(pdf: bytes, max_pages: int) -> None:
    if not pdf.startswith(b"%PDF-"):
        raise HTTPException(415, "only PDF files are accepted")
    try:
        document = pdfium.PdfDocument(pdf)
    except pdfium.PdfiumError as error:
        raise HTTPException(422, f"could not open the PDF: {error}") from error
    try:
        pages = len(document)
    finally:
        document.close()
    if not 1 <= pages <= max_pages:
        raise HTTPException(422, f"PDF has {pages} pages; the limit is {max_pages}")


def default_process_factory(settings: Settings, store: Store) -> Process:
    """The real pipeline: a model provider, OCR, the vendor master, and calibration."""
    import json

    from doc_extract.extraction.llm import build_provider
    from doc_extract.ingestion.ocr import RapidOcrEngine
    from doc_extract.service.pipeline import PipelineConfig, process_document
    from doc_extract.validation.reference import VendorMaster

    provider = build_provider(settings.provider)
    ocr = RapidOcrEngine()  # one per worker: the OCR engine is not shared across threads
    calibration = None
    if settings.calibration_path:
        raw = json.loads(settings.calibration_path.read_text())
        calibration = {float(level): accuracy for level, accuracy in raw.items()}
    config = PipelineConfig(
        settings.primary,
        settings.secondary,
        settings.repair,
        settings.approve_threshold,
        VendorMaster.load(settings.vendors_path) if settings.vendors_path else None,
        calibration,
    )

    def process(pdf: bytes, doc_id: str):
        return process_document(pdf, doc_id, provider, ocr, config, store.duplicate_index())

    return process


def _document_or_404(store: Store, doc_id: str) -> dict:
    document = store.get(doc_id)
    if document is None:
        raise HTTPException(404, "no such document")
    return document


def _safe_name(filename: str | None) -> str | None:
    """Keep the last path part only, so a stored name can never act as a path."""
    if not filename:
        return None
    return filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or None
