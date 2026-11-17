# ADR 0009: JSON logs, Prometheus metrics, per-document cost, and a daily budget

**Status:** Accepted

**Context:** A running service has to answer questions the offline evaluation cannot: is it up, is it keeping up, what is it costing, and is it still as good as when it was measured? An LLM service adds two problems a normal web service does not have. Its cost depends on tokens, which vary by document and model and can run away (a retry loop, a model with thinking turned on). And its quality can drift with no error at all: a provider updates a model, a new supplier sends a new layout, and approvals quietly fall or wrong values quietly rise.

**Options:**

- Logs: plain text; JSON lines with a request id and document id carried through.
- Metrics: a client library (prometheus_client, OpenTelemetry); a small exporter written here.
- Tracing: OpenTelemetry spans to a collector; an LLM-specific platform (Langfuse, LangSmith); timings and calls stored with each result.
- Cost: from the provider's billing console; computed from token counts per call.
- Quality in production: none until someone complains; the signals the service already has.

**Decision:**

- **Logs** are one JSON object per line. Context variables carry the request id (taken from `X-Request-ID` if it is safe, otherwise generated, and returned in the response) and the document id, so every line from an upload or from a worker can be joined. Logs never contain document content: ids, codes, counts, timings, and costs only.
- **Metrics** are exported at `/metrics` in the Prometheus text format by about a hundred lines of code, so a scrape holds nothing hidden. Labels come from small fixed sets: route templates, never paths with ids; model names, never file names. `/metrics` needs an API key, sent by Prometheus as a bearer token, because invoice volume is business information.
- **Per-document traces** are the pipeline's stage timings and every model call (method, model, tokens, seconds, error, cost), stored with the result and returned by the API. This answers "why was this one slow or expensive?" without a tracing backend. OpenTelemetry is the next step when there is more than one service to follow a request through.
- **Cost** is computed from tokens and a price table set in configuration (`DOC_EXTRACT_PRICES`). A model with no configured price has an unknown cost, never zero, and `/v1/stats` counts unpriced documents so the gap is visible. Local models cost nothing per token.
- **A daily budget** (`DOC_EXTRACT_DAILY_BUDGET_USD`) is checked before each document is claimed. When it is spent, workers wait until midnight UTC and uploads are still accepted. A document's cost is only known after its calls, so spend can overshoot by at most one document per worker.
- **Quality without labels:** the automation rate (share auto-approved) and the correction rate (share of reviewed documents where a reviewer changed a field). Both come from data the service already has. A falling automation rate means inputs got harder or the model changed. A rising correction rate means the review queue is right to exist; a correction rate near zero means the policy is too strict and is wasting reviewers' time.
- **Alerts** (in `monitoring/alerts.yml`) fire on what needs a person: a growing queue, a failing provider, any unexpected error, a spent budget, and automation falling below half.
- **Database migrations** are numbered and tracked in SQLite's `user_version`, so the cost and timing columns are added to an existing Phase 7 database in place.

**Consequences:** Metrics live in process memory, so counters reset on restart; Prometheus handles that for rates, but the totals in `/v1/stats` come from the database. Cost is an estimate from token counts; the provider's bill is the truth and should be reconciled monthly. The automation and correction rates are proxies: a model that becomes confidently wrong in the same way for both extractions would raise automation and hide its errors, which only a periodic labelled sample can catch. That audit (re-scoring a random sample of approved invoices by hand each month) is a process, not code, and is the most important thing this ADR cannot automate.
