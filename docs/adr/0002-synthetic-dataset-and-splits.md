# ADR 0002: Synthetic invoices with a held-out layout split

**Status:** Accepted

**Context:** Evaluation, few-shot examples, and later fine-tuning all need documents with exactly correct labels. Hand-labelling a few hundred real invoices is slow, and real invoices cannot be published (ADR 0001).

**Options:** Hand-label public scans; use a public dataset only; generate invoices from labels.

**Decision:**

- Generate the label first, then render the PDF from it, so labels are correct by construction. Labels hold canonical values (dates, `Decimal`, state names), and the renderer picks how to print them: label wording, date format, and Indian, Western, or no digit grouping.
- Draw GST rates from the slabs in force on the invoice date, since the 12% and 28% slabs ended on 22 September 2025.
- Give each document its own seed (`<dataset seed>-<index>`), so any document can be rebuilt alone and adding documents never changes existing ones.
- Assign `train`, `dev`, and `test` by hashing the document id (70/15/15). Put the `modern` layout only in a fourth split, `test_layout`, so one score measures generalisation to a layout no prompt or model was tuned on.

**Consequences:** Datasets are reproducible from a seed, so `data/` stays out of git. Synthetic invoices are cleaner than real ones: every PDF has a text layer, addresses do not match their cities, and there are no stamps, handwriting, or skew. Scores on them are an upper bound. Scan simulation (Phase 2) and a public receipt dataset are needed for a harder test.
