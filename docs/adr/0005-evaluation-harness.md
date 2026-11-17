# ADR 0005: Field-level precision and recall, matched line items, bootstrap intervals

**Status:** Accepted

**Context:** Every later phase (the agent, confidence, fine-tuning) has to prove it beats what came before. That needs one scorer, applied the same way to every run, that says which fields fail and whether a difference is real on a dev set of about 30 documents.

**Options:**

- Matching: exact string match everywhere; normalised match per field type; an LLM as judge.
- Line items: compare by position; pair rows by best agreement.
- Uncertainty: report point estimates; bootstrap intervals; more data only.

**Decision:**

- Compare per field type. Identifiers (invoice number, HSN) match exactly apart from spaces, and GSTINs also ignore case, so one wrong character is wrong. Amounts and rates compare as numbers. Dates must be equal dates. States accept a name, a code, or both. Names, addresses, and descriptions match at a fuzzy similarity of 90 or more.
- No LLM judge. Every field here has a checkable right answer, and a judge would add cost, run-to-run variation, and its own errors to the measurement.
- Score each field as correct, missed, wrong, hallucinated, or correctly null. A wrong value counts against both precision and recall. Report per-field, header, line-item, and micro F1, plus the exact-document rate: the share of invoices needing no correction.
- Pair predicted and gold line items by the assignment that maximises agreeing fields (exact search up to 14 rows, greedy above), so one skipped row costs one row.
- Give each headline number a 95% bootstrap interval over documents. Compare runs with a paired bootstrap over shared documents.
- Keep a regex baseline (`--method rules`). It gives real numbers without an API key, and in CI it acts as an end-to-end regression test of rendering, text layers, row rebuilding, and scoring.

**Consequences:** Scores are deterministic and free to recompute. The fuzzy threshold is a judgement call: a name with one OCR slip in 20 characters counts as right, two slips may not. Micro F1 is dominated by line items, which hold about 60% of gold values, so header and line-item F1 are reported separately. Bootstrap intervals assume the dev documents represent the real mix, which synthetic data does not guarantee.
