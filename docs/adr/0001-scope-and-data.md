# ADR 0001: Indian GST invoices, synthetic and public data, review before any action

**Status:** Accepted

**Context:** The project must show agents, evaluation, and deployment on a realistic business workflow. Real invoices contain names, tax IDs, bank details, and amounts, and the code will be public.

**Options:**

- Scope: Indian GST invoices; receipts; any document type.
- Data: real invoices; public datasets only; synthetic invoices plus public datasets.
- Output: post results to downstream systems; return results for human review.

**Decision:**

- Start with Indian GST invoices. Add receipts from a public dataset later to test a second layout family.
- Generate synthetic invoices with known labels, and download public datasets by script. Never commit real documents, and never commit dataset files.
- The system extracts and validates only. It never pays, approves, or posts anything. Every result is either auto-approved by a stated confidence policy or sent to a human.

**Consequences:** Anyone can reproduce the results. Synthetic invoices are cleaner and more regular than real ones, so scores on them will be optimistic. The public dataset and a small set of hand-made messy scans are needed as a harder test. Because the agent has no write access outside its own database, a prompt injection inside a document can at worst corrupt one extraction, which validation and review are there to catch.
