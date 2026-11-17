# Document Extraction Agent

Extracts structured, validated data from Indian GST invoices. An agent checks its own output with tools, sends uncertain documents to a human, and never takes any action outside its own database.

**Status:** the pipeline is built and measured (synthetic data, scans, OCR, text and vision extraction, validation, repair, a tool-calling agent, calibrated confidence), and runs as a deployable service with a review queue, metrics, cost tracking, and an MCP server for AI assistants. See [the progress file](PROJECT_PROGRESS.md) and [the evaluation](docs/EVALUATION.md).

```
PDF upload ─▶ queue (SQLite) ─▶ worker: vision extraction ─▶ rule + vendor checks ─▶ repair
                                        text extraction ─────▶ agreement ─▶ calibrated confidence
                                                                               │
                              auto-approve ◀── no failed check, weakest field ≥ 0.98
                              review queue ◀── otherwise, with the fields to check and why
```

## Setup

Requires Python 3.12 or later.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

On Debian or Ubuntu, `python3 -m venv` needs the `python3-venv` package; `virtualenv .venv` works without it.

## Synthetic dataset

```bash
.venv/bin/doc-extract synth --count 300 --seed v1
```

This writes a PDF, its gold label (`.json`), and a `manifest.jsonl` row for each invoice to `data/synthetic`. The same seed always gives the same files. The `modern` layout appears only in the `test_layout` split, to measure performance on an unseen layout.

## Scans and OCR

```bash
.venv/bin/doc-extract scan --severity medium
.venv/bin/doc-extract ocr-eval --data data/scanned/medium --split dev
```

PDFs with a text layer are read directly. Image-only pages go through RapidOCR, which runs on CPU and needs no system packages. Results are in [the evaluation](docs/EVALUATION.md).

## Extraction

```bash
export GEMINI_API_KEY=your_key
.venv/bin/doc-extract extract --method text --split dev --limit 5
.venv/bin/doc-extract score --predictions eval/results/pred-text-synthetic-dev-gemini.jsonl
```

`--method text` sends rebuilt page text; `--method vision` sends the page image; `--method rules` uses regular expressions and needs no key. Predictions are saved one per line, so an interrupted run continues where it stopped. On the Gemini free tier, Google may use submitted content to improve its products, so send only synthetic invoices there; use `--provider ollama` to keep documents on your machine.

## Scoring

```bash
.venv/bin/doc-extract score --predictions eval/results/pred-rules-synthetic-dev-rules.jsonl
.venv/bin/doc-extract compare --baseline <run A>.jsonl --candidate <run B>.jsonl
```

`score` prints precision, recall, and F1 for every field, with bootstrap intervals for the headline numbers. Add `--input-price` and `--output-price` (USD per million tokens) for cost per document, and `--fail-under 0.9` to exit with an error below a micro F1. `compare` runs a paired bootstrap over the documents both runs share.

## Validation and the agent

```bash
.venv/bin/doc-extract vendors
.venv/bin/doc-extract score --predictions <run>.jsonl --validation --vendors data/vendors.json
.venv/bin/doc-extract agent --mode repair --method vision --data data/scanned/heavy --vendors data/vendors.json
.venv/bin/doc-extract agent --mode tools --data data/scanned/heavy --vendors data/vendors.json
```

Validation runs fixed checks on every extraction: required fields, GSTIN checksum and state, line arithmetic, tax type against place of supply, tax amounts, grand total, rate slabs, and dates. With a vendor master it also flags unknown sellers, which catches swapped seller and buyer GSTINs, and duplicates. `--mode repair` re-asks the model with the failed checks, up to two times. `--mode tools` runs a tool-calling agent that reads text, views zoomed parts of the page, validates its draft, looks up vendors, and submits. Every tool is read-only.

`doc-extract reparse <run>.jsonl` re-parses saved model output with the current parser, so parser fixes apply to old runs without new API calls.

## Confidence and review

```bash
.venv/bin/doc-extract calibrate --primary <run>.jsonl --secondary <second run>.jsonl --data <dataset> --vendors data/vendors.json
.venv/bin/doc-extract review --primary <run>.jsonl --secondary <second run>.jsonl --vendors data/vendors.json --calibration eval/calibration.json
```

Each field gets a confidence from agreement between two extractions, failed checks, repair history, and the vendor master. `calibrate` maps those levels to measured accuracy. `review` approves an invoice only when no check fails and every field reaches the threshold (default 0.98), and lists the weakest fields, with reasons, for everything else.

## Service

```bash
export GEMINI_API_KEY=your_key
export DOC_EXTRACT_API_KEYS="alice:$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export DOC_EXTRACT_VENDORS=data/vendors.json
.venv/bin/doc-extract serve
```

Upload a PDF, then poll for the result. Interactive API docs are at `http://127.0.0.1:8000/docs`.

```bash
curl -H "X-API-Key: $KEY" -F file=@invoice.pdf localhost:8000/v1/documents   # 202 {"id": ...}
curl -H "X-API-Key: $KEY" localhost:8000/v1/documents/<id>                    # status, decision, invoice
curl -H "X-API-Key: $KEY" localhost:8000/v1/review-queue                      # what needs a person
curl -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"action": "approve", "corrections": {"totals.grand_total": "1180.00"}}' \
  localhost:8000/v1/documents/<id>/review
```

Uploads return at once; background workers run vision extraction with repair, a text extraction as a second opinion, the checks, and the approval policy. A provider outage is retried with backoff; a poor answer goes to review. Every review is recorded with the reviewer (taken from the API key), the time, and each field's value before and after. Settings are environment variables, listed in [.env.example](.env.example).

## Deployment

```bash
cp .env.example .env        # fill in GEMINI_API_KEY and DOC_EXTRACT_API_KEYS
docker compose up --build   # API on 127.0.0.1:8000, data in a named volume
```

The image is two-stage and runs as an unprivileged user. `/health` is liveness and `/ready` is readiness: a missing model key keeps the API up but not ready. CI builds the image and waits for its health check.

## Observability and cost

- Logs are JSON lines with a request id and document id, and never include document content.
- `/metrics` exports Prometheus metrics: requests, stage latency, tokens and cost by model, decisions, failures, queue depth. `docker compose --profile monitoring up` adds Prometheus with [alert rules](monitoring/alerts.yml).
- `/v1/stats` shows the automation rate, the correction rate (how often reviewers change something), today's cost, cost per document, and p50/p95 latency.
- Set `DOC_EXTRACT_PRICES="gemini-3.5-flash-lite=<in>/<out>"` (USD per million tokens, from the provider's price page) for costs, and `DOC_EXTRACT_DAILY_BUDGET_USD` to pause workers when a day's spend is reached.

## MCP server

`doc-extract mcp` lets an AI assistant submit invoices, read results and the review queue, check an invoice against the rules, and look up vendors. It calls the service with its own API key, reads PDFs only from the folders you allow, and has no tool to approve or reject anything. For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "doc-extract": {
      "command": "/path/to/doc_extract_agent/.venv/bin/doc-extract",
      "args": ["mcp"],
      "env": {
        "DOC_EXTRACT_URL": "http://127.0.0.1:8000",
        "DOC_EXTRACT_API_KEY": "<a key from DOC_EXTRACT_API_KEYS>",
        "DOC_EXTRACT_MCP_ROOTS": "/path/to/invoices",
        "DOC_EXTRACT_VENDORS": "/path/to/doc_extract_agent/data/vendors.json"
      }
    }
  }
}
```

## Decisions

Each major decision has a short record in [docs/adr](docs/adr).
