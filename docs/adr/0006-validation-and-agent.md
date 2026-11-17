# ADR 0006: Rule validation everywhere, repair and agent measured against one call

**Status:** Accepted

**Context:** Extraction makes errors that a reviewer would otherwise have to find. Two questions: how can errors be detected without gold labels, and is it worth spending more model calls to fix them? An agent is the fashionable answer to the second, but it has to beat a single call by enough to pay for itself.

**Options:**

- Detection: rule checks; reference data (vendor master, past invoices); asking an LLM whether the extraction is right.
- Correction: none, so flagged invoices go to a human; a fixed repair step that re-asks with the failed checks; a tool-calling agent that decides what to look at.

**Decision:**

- Run rule checks on every extraction: required fields, GSTIN format, checksum, and state code, line arithmetic, the sum of lines, tax type against place of supply, tax amount from line rates, grand total, round off, rate slabs for the date, and dates. Rules are deterministic, free, and explainable to an auditor. An LLM judge is none of these.
- Add reference checks when the data exists: an unknown seller GSTIN is an error, which catches swapped seller and buyer GSTINs that every rule accepts; an exact repeat of seller and invoice number is a duplicate.
- Measure validation as a detector, by the share of wrong invoices it flags and its false alarms on correct ones.
- Build both correction designs. Repair: fixed control flow, at most two extra calls, with the failed checks and vendor-master hints in the prompt. Agent: the model calls `read_text`, `view_page` (a zoomed part of the page), `validate`, `lookup_vendor`, and `submit`, within eight turns.
- Make every tool read-only, so a prompt injection in a document can corrupt only that document's extraction.
- Both prompts forbid changing a number to make a check pass. Comparisons report correct values fixed and broken separately, so a repair that games the checks is visible.

**Consequences:** Rule checks cannot see errors that are internally consistent: a misread invoice number, a truncated address. Only reference data or a human can. The agent resends its whole history each turn, including every image, so its cost grows faster than its number of turns. Whether either correction design is worth its cost is a measured question, recorded below.

**Outcome, 2026-11-16 (heavy scans, dev):** Validation flagged 29 of 30 wrong invoices with no false alarms. Repair raised micro F1 from 0.873 to 0.894 (paired interval +0.007 to +0.035) and exact invoices from 3 to 7, for 3.5 times the input tokens; it is kept, and the service uses it. On the 11 invoices it has finished, the agent scored below a single call (0.884 against 0.893) at about 30 times the input tokens, so it stays an experiment and is not used in the service. The remaining 18 agent runs will settle the number, not the decision unless they differ sharply.
