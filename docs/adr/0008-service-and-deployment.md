# ADR 0008: One container with an HTTP API, SQLite as store and queue, background workers

**Status:** Accepted

**Context:** Phases 3 to 6 run as batch commands over a dataset. A business needs to send one invoice at a time, get a decision, and have a person correct the uncertain ones, with a record of who changed what. One document takes several model calls and OCR, 10 to 60 seconds, which is too long to hold an HTTP request open. The project is run by one person, so every extra moving part has to earn its place.

**Options:**

- Interface: a synchronous endpoint that returns the result; an upload that returns at once and is processed in the background, with polling; webhooks to the caller.
- Queue: a broker (Redis, RabbitMQ, a cloud queue) with separate worker processes; the database table as the queue, with workers as threads in the API process.
- Store: PostgreSQL; SQLite.
- Deployment: a managed platform's buildpack; a container image run anywhere.

**Decision:**

- Upload returns `202` with an id; workers process in the background; clients poll `GET /v1/documents/{id}`. Webhooks are easy to add later and need retry logic of their own, so not yet.
- The `documents` table is the queue. A worker claims the oldest ready row with a single `UPDATE ... RETURNING`, so two workers never take the same document. A crash leaves the row in `processing`, and the next start puts it back. No broker to run or lose messages in.
- SQLite in WAL mode, which lets the API read while a worker writes. PDFs are files beside the database, named by id. The schema is plain SQL, so moving to PostgreSQL changes the connection, the `RETURNING` claim becomes `FOR UPDATE SKIP LOCKED`, and the rest stays.
- The production pipeline is the measured winner: vision first, repair against failed checks, text as an independent second extraction, then the Phase 6 policy (ADR 0007). Each part can be switched off in configuration.
- Failures are split by cause. No answer from the provider (outage, rate limit, bad key) is retried with exponential backoff, at most three attempts. A poor answer is a fact about the document and goes to review, never into a retry loop. A failed second extraction does not fail the document; it lowers confidence, so it cannot be approved automatically.
- Same bytes uploaded twice return the first id (SHA-256), so a client that retries an upload does not pay twice. A different file with the same seller and invoice number as an approved invoice is flagged as a duplicate.
- Reviewers approve or reject, with corrections keyed exactly like `fields_to_check`. Corrections pass through the same invoice model as extraction, so a reviewer cannot store a value the pipeline would reject. Approving an invoice that still fails a check needs a note. Every review is appended with reviewer, time, and each field's value before and after.
- API keys map to names, and the reviewer recorded is the key's name, never a name sent in the request. Keys are compared in constant time. The service refuses to start without keys unless anonymous access is set explicitly.
- Uploads are capped in size before they are read in full, must start with `%PDF-`, must open in pdfium, and may have at most five pages. Stored file names keep only their last path part.
- A two-stage Docker image runs as an unprivileged user, with a health check. `/health` says the process is up; `/ready` says the database answers and a worker is running. A missing model key keeps the API up but not ready, so the problem is visible rather than a crash loop.

**Consequences:** Workers are threads in one process, so the service scales up (more workers) but not out to several machines while it uses SQLite. That is enough for thousands of invoices a day at these latencies. Only the first page is extracted, which fits single-page invoices, not multi-page ones. API keys give one level of access: anyone with a key can upload and review; roles (uploader, reviewer, admin) would come with a real identity provider. Polling is simple but chatty. Reviewer corrections are stored but not yet used to improve prompts or calibration.
