# Project progress

**Objective:** Extract validated, structured data from invoices and other business documents, with an agent that checks its own work, human review for uncertain cases, and a deployed, observable service.

**Status on 2026-11-16:** Phases 0 to 9 are built, with 1,027 tests passing. The pipeline runs as a service: upload, background workers, approve-or-review, a review API with an audit trail, Docker deployment, JSON logs, Prometheus metrics, per-document cost with a daily budget, and an MCP server for AI assistants. One measurement is still open: the tool-calling agent has run on 15 of 33 heavy scans (11 completed; so far about 30 times the tokens of one call with no gain, recorded in ADR 0006). Phase 10 is skipped for now; the study PDFs for Phase 11 are in `study/`.

## Phases

| # | Phase | Status |
| --- | --- | --- |
| 0 | Scope and setup | Done |
| 1 | Dataset and labels | Done |
| 2 | Document ingestion | Done |
| 3 | Extraction baseline | Done |
| 4 | Evaluation harness | Done |
| 5 | Validation tools and agent loop | Built; agent run 15 of 33 |
| 6 | Confidence and human review | Done |
| 7 | Service and deployment | Done |
| 8 | Observability and cost | Done |
| 9 | MCP server | Done |
| 10 | Fine-tuning a small model (stretch) | Skipped for now |
| 11 | Polish and study PDFs | Study PDFs done |

## Decisions

| Decision | Choice | Record |
| --- | --- | --- |
| Scope and data | GST invoices, synthetic plus public data, review before action | ADR 0001 |
| Dataset | Labels first, rendered PDFs, seeded per document, held-out layout split | ADR 0002 |
| Ingestion | Text layer first, RapidOCR for images, skew from box angles | ADR 0003 |
| Extraction | Text and vision methods, schema-constrained JSON, scored offline | ADR 0004 |
| Evaluation | Typed field comparison, matched line items, bootstrap intervals, regex baseline | ADR 0005 |
| Validation and agent | Rule and reference checks; repair and agent measured against one call | ADR 0006 |
| Confidence and review | Agreement and checks as signals, calibrated, approve on the weakest field | ADR 0007 |
| Service | Async upload, SQLite as store and queue, worker threads, API keys, Docker | ADR 0008 |
| Observability | JSON logs, Prometheus metrics, cost from tokens, daily budget, automation and correction rates | ADR 0009 |
| MCP server | Client of the service, allowed folders only, no approve or reject tool | ADR 0010 |

## Next steps

1. Finish the agent run on heavy scans (18 documents left, needs your Gemini key), score it against vision and repair, and record whether it is worth its cost in ADR 0006 and `docs/EVALUATION.md`.
2. Run the service once against Gemini with a few synthetic invoices (`doc-extract serve`, then upload), and note real latency and cost per document in `docs/EVALUATION.md`.
3. Study `study/01-project-guide.pdf`, then `study/02-interview-qa.pdf` (built from `study/exercises.md`, the code, ADRs, and `docs/EVALUATION.md`).
4. Optional later: Phase 10, fine-tuning a small open vision model on the train split, compared with Gemini on cost, latency, and F1.
5. Optional: add the CORD receipt dataset (CC BY 4.0) to test a second document type.

## Known issues

- Model ids change often: the `-latest` alias returned 503s, and `gemini-2.5-flash` is closed to new users. The default is pinned to `gemini-3.5-flash-lite`.
- OCR misreads `TI` as `Tl` in invoice numbers, even on clean renders.
- Heavy scans defeat OCR: only 5% of GSTINs survive. Vision does far better but still gets only about a quarter of GSTINs right.
- Rule validation cannot see internally consistent errors, such as a truncated address or a misread invoice number.
- A text layer is trusted without checking, so a PDF with a broken font mapping would not fall back to OCR.
- The service extracts only the first page, which fits single-page invoices.
- Docker Hub was unreachable from the build machine, so the image itself has not been built yet; CI builds it and checks its health endpoint on the first push. `doc-extract serve` was tested as a live process.
- API keys give one level of access; there are no separate uploader and reviewer roles.

## Interview topics to revise

From Phase 0: separating extraction from validation, money as `Decimal`, why every extracted field is optional, field descriptions as LLM instructions, limiting what an agent is allowed to do.

From Phase 1: generating labels before documents, canonical labels vs printed values, GSTIN checksum and its limits, half-up vs banker's rounding, per-line tax rounding, seeded and stable splits, held-out layout as a generalisation test, what synthetic data cannot teach.

From Phase 2: text layer vs OCR, rebuilding rows from boxes, skew and how box angles fix it, timestamps breaking reproducibility, CER vs key-value recall, I/l confusion, choosing an OCR engine.

From Phase 3: amounts as strings, required-but-nullable keys, copy-don't-correct prompts, schema and prompt consistency, saving predictions apart from scoring, constrained decoding limits, thinking tokens and cost.

From Phase 4: why a wrong value is both a false positive and a false negative, typed comparison, line-item assignment, micro vs split F1, bootstrap and paired bootstrap, no LLM judge for checkable fields, regression tests that find real bugs, exact-document rate.

From Phase 5: what rules can and cannot detect, reference data catching a GSTIN swap, false alarms and review load, repair loops gaming checks, agent token growth, agent vs pipeline, read-only tools as the injection defence, thought signatures, lenient parsing.

From Phase 6: confidence from outside the model, calibration by histogram binning, fitting and testing on different documents, weakest field vs average, the auto-approve threshold as a business choice, targeted review.

From Phase 7: async upload and polling, the database as a queue, atomic claim, crash recovery, transient vs permanent failures, backoff, idempotent uploads by hash, audit trail, constant-time key comparison, upload validation, liveness vs readiness, two-stage images, non-root containers.

From Phase 8: structured logs with context variables, no content in logs, metric label cardinality, histograms and percentiles, cost from tokens, unknown vs zero cost, budget guards and overshoot, quality without labels (automation and correction rates), alerts, schema migrations.

From Phase 9: what MCP is, tools vs resources, tool annotations, the server as a client of the API, least privilege for agents, path traversal and symlinks, prompt injection through documents, why there is no approve tool.

## Resume here (2026-11-16)

1. Make commits 1 to 37 from `COMMITS.md`, in order. Nothing is committed yet. Each one was replayed in a scratch clone with lint and tests passing.
2. Finish the agent run (each command continues where it stopped):

   ```bash
   eval "$(grep '^export GEMINI_API_KEY=' ~/.bashrc)" && .venv/bin/doc-extract agent --mode tools --data data/scanned/heavy --split dev --vendors data/vendors.json
   ```

   Then score it against `pred-vision-heavy-dev-gemini.jsonl` and `repair-vision-vendors-heavy-dev.jsonl` with `score` and `compare`, and add the outcome to ADR 0006 and `docs/EVALUATION.md`.
3. Try the service against Gemini (README, "Service"), and push once to see CI build the Docker image.
4. Study the two PDFs in `study/`.
