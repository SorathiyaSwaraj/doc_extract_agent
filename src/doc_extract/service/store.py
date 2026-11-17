"""Documents, results, and review history in SQLite; PDFs as files beside it.

SQLite is enough for one machine and a few documents a second, needs no server,
and its WAL mode lets the API read while a worker writes. The table doubles as
the job queue: a document is claimed by flipping its status inside one
transaction, so two workers can never take the same one, and a crash leaves a
row in `processing` that the next start puts back in the queue.

The schema changes by numbered migrations, tracked in SQLite's user_version, so a
database from an older release is upgraded in place when the service starts.

Reviews are append-only. The current invoice can change after a correction, but
every change keeps who made it, when, and the value before and after.
"""

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doc_extract.models import Invoice
from doc_extract.validation.reference import DuplicateIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    filename TEXT,
    size INTEGER NOT NULL,
    status TEXT NOT NULL,         -- queued, processing, done, failed
    decision TEXT,                -- approve or review, from the pipeline
    final TEXT,                   -- approved or rejected, by policy or a reviewer
    confidence REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,   -- not claimed before this time; backoff after failures
    error TEXT,
    result TEXT,                  -- pipeline outcome, JSON
    invoice TEXT,                 -- current invoice after any corrections, JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_status ON documents (status, available_at);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents (id),
    reviewer TEXT NOT NULL,
    action TEXT NOT NULL,         -- approve, reject
    corrections TEXT NOT NULL,    -- field -> {before, after}, JSON
    note TEXT,
    created_at TEXT NOT NULL
);
"""

# Each entry runs once, in order, inside one transaction. Never edit an entry that has
# shipped: add a new one.
MIGRATIONS = (
    SCHEMA,
    """
    ALTER TABLE documents ADD COLUMN cost_usd REAL;
    ALTER TABLE documents ADD COLUMN seconds REAL;
    ALTER TABLE documents ADD COLUMN processed_at TEXT;
    CREATE INDEX documents_processed ON documents (processed_at);
    """,
)

JSON_COLUMNS = ("result", "invoice")


def now(delay_seconds: float = 0) -> str:
    moment = datetime.now(UTC) + timedelta(seconds=delay_seconds)
    return moment.isoformat(timespec="milliseconds")


class Store:
    def __init__(self, db_path: Path, storage_dir: Path) -> None:
        self.db_path, self.storage_dir = db_path, storage_dir
        db_path.parent.mkdir(parents=True, exist_ok=True)
        storage_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self.migrate(db)

    @staticmethod
    def migrate(db: sqlite3.Connection) -> int:
        """Apply migrations the database has not seen. Returns the new schema version."""
        version = db.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            db.executescript(f"BEGIN IMMEDIATE; {sql}; PRAGMA user_version = {number}; COMMIT;")
        return len(MIGRATIONS)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # A connection per operation: sqlite3 connections must not cross threads.
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def pdf_path(self, doc_id: str) -> Path:
        return self.storage_dir / f"{doc_id}.pdf"

    def add(self, pdf: bytes, filename: str | None) -> tuple[str, bool]:
        """Store a new upload. The same bytes twice return the first id and False."""
        digest = hashlib.sha256(pdf).hexdigest()
        doc_id = uuid.uuid4().hex
        stamp = now()
        with self._connect() as db:
            row = db.execute("SELECT id FROM documents WHERE sha256 = ?", (digest,)).fetchone()
            if row:
                return row["id"], False
            # Write the file first: a row must never point at a missing PDF.
            self.pdf_path(doc_id).write_bytes(pdf)
            try:
                db.execute(
                    "INSERT INTO documents (id, sha256, filename, size, status, available_at, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (doc_id, digest, filename, len(pdf), stamp, stamp, stamp),
                )
            except sqlite3.IntegrityError:
                # Another request stored the same bytes between our SELECT and INSERT.
                self.pdf_path(doc_id).unlink(missing_ok=True)
                row = db.execute("SELECT id FROM documents WHERE sha256 = ?", (digest,)).fetchone()
                return row["id"], False
        return doc_id, True

    def claim_next(self) -> dict | None:
        """Take the oldest ready document and mark it processing, atomically."""
        stamp = now()
        with self._connect() as db:
            row = db.execute(
                "UPDATE documents SET status = 'processing', attempts = attempts + 1, "
                "updated_at = ? WHERE id = (SELECT id FROM documents WHERE status = 'queued' "
                "AND available_at <= ? ORDER BY available_at LIMIT 1) RETURNING *",
                (stamp, stamp),
            ).fetchone()
        return _to_dict(row)

    def requeue_stale(self) -> int:
        """Put back documents left in processing by a crash. Call before workers start."""
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE documents SET status = 'queued', updated_at = ? "
                "WHERE status = 'processing'",
                (now(),),
            )
        return cursor.rowcount

    def complete(self, doc_id: str, outcome: dict) -> None:
        final = "approved" if outcome["decision"] == "approve" else None
        stamp = now()
        with self._connect() as db:
            db.execute(
                "UPDATE documents SET status = 'done', decision = ?, final = ?, confidence = ?, "
                "error = NULL, result = ?, invoice = ?, cost_usd = ?, seconds = ?, "
                "processed_at = ?, updated_at = ? WHERE id = ?",
                (
                    outcome["decision"],
                    final,
                    outcome["confidence"],
                    json.dumps(outcome, default=str),
                    json.dumps(outcome["invoice"]) if outcome["invoice"] else None,
                    outcome.get("cost_usd"),
                    round(sum(outcome.get("stages", {}).values()), 3),
                    stamp,
                    stamp,
                    doc_id,
                ),
            )

    def fail(self, doc_id: str, error: str, retry: bool, delay_seconds: float = 0) -> None:
        """Record a failure. With retry, the document is queued again after the delay."""
        status = "queued" if retry else "failed"
        with self._connect() as db:
            db.execute(
                "UPDATE documents SET status = ?, error = ?, available_at = ?, updated_at = ? "
                "WHERE id = ?",
                (status, error[:1000], now(delay_seconds), now(), doc_id),
            )

    def get(self, doc_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if row is None:
                return None
            reviews = db.execute(
                "SELECT reviewer, action, corrections, note, created_at FROM reviews "
                "WHERE document_id = ? ORDER BY id",
                (doc_id,),
            ).fetchall()
        document = _to_dict(row)
        document["reviews"] = [
            {**dict(r), "corrections": json.loads(r["corrections"])} for r in reviews
        ]
        return document

    def documents(
        self,
        status: str | None = None,
        decision: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        where, params = [], []
        for column, value in (("status", status), ("decision", decision)):
            if value:
                where.append(f"{column} = ?")
                params.append(value)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, filename, status, decision, final, confidence, attempts, error, "
                f"created_at, updated_at FROM documents {clause} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def review_queue(self, limit: int = 50) -> list[dict]:
        """Documents waiting for a person, least confident first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, filename, confidence, result, created_at FROM documents "
                "WHERE status = 'done' AND decision = 'review' AND final IS NULL "
                "ORDER BY confidence, created_at LIMIT ?",
                (limit,),
            ).fetchall()
        queue = []
        for row in rows:
            result = json.loads(row["result"])
            queue.append(
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "confidence": row["confidence"],
                    "reasons": result["reasons"],
                    "fields_to_check": result["fields_to_check"],
                    "created_at": row["created_at"],
                }
            )
        return queue

    def add_review(
        self,
        doc_id: str,
        reviewer: str,
        action: str,
        corrections: dict,
        invoice: dict | None,
        note: str | None,
    ) -> None:
        final = "approved" if action == "approve" else "rejected"
        stamp = now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO reviews (document_id, reviewer, action, corrections, note, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, reviewer, action, json.dumps(corrections, default=str), note, stamp),
            )
            db.execute(
                "UPDATE documents SET final = ?, invoice = ?, updated_at = ? WHERE id = ?",
                (final, json.dumps(invoice) if invoice else None, stamp, doc_id),
            )
            db.execute("COMMIT")

    def duplicate_index(self) -> DuplicateIndex:
        """Seller and invoice number of every approved invoice, for duplicate checks."""
        index = DuplicateIndex()
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, invoice FROM documents WHERE final = 'approved' AND invoice IS NOT "
                "NULL ORDER BY created_at"
            ).fetchall()
        for row in rows:
            index.add(Invoice.model_validate_json(row["invoice"]), row["id"])
        return index

    def spent_since(self, start: str) -> float:
        """Model spend, in USD, on documents processed at or after an ISO time."""
        with self._connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM documents WHERE processed_at >= ?",
                (start,),
            ).fetchone()
        return row[0]

    def status_counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall()
        return {status: count for status, count in rows}

    def stats(self, since: str, latency_window: int = 1000) -> dict:
        """What an operator needs on one screen: volume, automation, quality, cost, speed."""
        with self._connect() as db:
            outcomes = db.execute(
                "SELECT decision, final, COUNT(*) FROM documents WHERE status = 'done' "
                "GROUP BY decision, final"
            ).fetchall()
            spend = db.execute(
                "SELECT COUNT(*), COUNT(cost_usd), COALESCE(SUM(cost_usd), 0) FROM documents "
                "WHERE processed_at >= ?",
                (since,),
            ).fetchone()
            seconds = [
                row[0]
                for row in db.execute(
                    "SELECT seconds FROM documents WHERE seconds IS NOT NULL "
                    "ORDER BY processed_at DESC LIMIT ?",
                    (latency_window,),
                )
            ]
            reviews = db.execute(
                "SELECT corrections FROM reviews WHERE id IN "
                "(SELECT MAX(id) FROM reviews GROUP BY document_id)"
            ).fetchall()
        done = sum(count for *_, count in outcomes)
        auto = sum(count for decision, _, count in outcomes if decision == "approve")
        waiting = sum(
            count for decision, final, count in outcomes if decision == "review" and not final
        )
        corrected = [len(json.loads(row[0])) for row in reviews]
        processed, priced, cost = spend
        return {
            "documents": self.status_counts(),
            "decided": done,
            "auto_approved": auto,
            "automation_rate": round(auto / done, 4) if done else None,
            "waiting_for_review": waiting,
            "reviewed": len(corrected),
            # Share of reviewed documents where the reviewer had to change something:
            # the error rate of what the pipeline sends to people.
            "correction_rate": (
                round(sum(n > 0 for n in corrected) / len(corrected), 4) if corrected else None
            ),
            "fields_corrected": sum(corrected),
            "since": since,
            "processed_since": processed,
            "cost_usd_since": round(cost, 6),
            "unpriced_documents_since": processed - priced,
            "cost_per_document_usd": round(cost / priced, 6) if priced else None,
            "seconds_p50": _percentile(seconds, 0.5),
            "seconds_p95": _percentile(seconds, 0.95),
        }

    def ping(self) -> None:
        with self._connect() as db:
            db.execute("SELECT 1").fetchone()


def _percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile: a value that really occurred, no interpolation."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))]


def _to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    document = dict(row)
    for column in JSON_COLUMNS:
        if document.get(column):
            document[column] = json.loads(document[column])
    return document
