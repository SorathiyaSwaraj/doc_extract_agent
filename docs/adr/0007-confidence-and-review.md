# ADR 0007: Confidence from agreement and checks, calibrated on held-out documents

**Status:** Accepted

**Context:** Every invoice needs a decision: approve it without a human, or send it to review. Approving errors means wrong payments and tax entries that nobody checked. Reviewing everything means the system saves no work. The decision needs a confidence for each field that can be trusted, and a reviewer needs to know which fields to check.

**Options:**

- Source of confidence: the model's own token probabilities; asking the model how sure it is; signals outside the model (a second extraction, validation, repair history, reference data).
- Scale: use the raw scores; calibrate them against measured accuracy.
- Document decision: average field confidence; weakest field; a learned classifier.

**Decision:**

- Build confidence from signals outside the model. A second, independent extraction (text alongside vision) that agrees is the strongest. A failed check naming the field, a change made during repair, and a value the parser dropped all lower it. A vendor master that holds the seller GSTIN under a matching name raises it. Token probabilities are not exposed for structured output by every provider, and models asked how sure they are tend to say "very".
- Treat the hand-set levels as raw scores, and calibrate them by histogram binning: each level becomes the accuracy measured for it on documents that were not used to measure the result.
- Approve a document only when it fails no check and its weakest field reaches 0.98. One wrong GSTIN is enough to make an invoice wrong, so an average would hide exactly the field that matters.
- For documents in review, list the fields below 0.95, weakest first, with reasons.

**Consequences:** Two extractions double the cost of every invoice. That is cheaper than review, and only where both methods are available. Calibration is fitted per document population: a mapping from heavy scans will not hold for clean PDFs, so each source of documents needs its own calibration set. The 0.98 threshold is a business choice and should be set from the auto-approve curve, not by engineers. Corrections from reviewers are not yet fed back into prompts or calibration.
