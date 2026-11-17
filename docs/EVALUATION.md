# Evaluation

All results use the synthetic dataset built with `doc-extract synth --count 300 --seed v1` and scanned copies from `doc-extract scan --severity <level> --seed v1`. Synthetic invoices are cleaner than real ones (ADR 0002), so treat every number here as an upper bound.

## OCR (Phase 2)

RapidOCR at 200 DPI on the dev split (33 invoices), compared with each clean PDF's text layer. Measured on 2026-11-07 on a laptop CPU.

```bash
.venv/bin/doc-extract ocr-eval --data data/scanned/medium --split dev
```

| Input | CER mean | CER max | Invoice no. | GSTIN | HSN/SAC | Amounts | Seconds per page |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Clean render | 0.09% | 0.38% | 93.9% | 100% | 100% | 100% | 5.1 |
| Light scan | 0.02% | 0.33% | 93.9% | 100% | 100% | 100% | 4.9 |
| Medium scan | 0.11% | 0.48% | 81.8% | 100% | 100% | 100% | 5.6 |
| Heavy scan | 32.1% | 71.8% | 21.2% | 5.2% | 52.3% | 27.8% | 4.7 |

The value columns show key-value recall: the share of gold values found in the OCR text, ignoring spaces and digit grouping. A value missing here cannot be extracted by the text method.

What this shows:

- **Light and medium scans read almost perfectly,** except for invoice numbers. On 33 documents, CER differences below 0.1% are noise.
- **Invoice numbers fail on letter shapes.** On clean renders, `TI/1045/2025-26` is read as `Tl/1045/2025-26`, because capital I and lowercase l look the same in Helvetica. The miss rate grows with scan damage. No checksum or arithmetic check can detect this.
- **Heavy scans break OCR.** At 100 DPI with blur and JPEG damage, only 5% of GSTINs survive. This is where the vision method has to prove itself.
- **Skew correction was required.** Before rows were grouped on skew-corrected heights, a medium scan at about 1.5 degrees split every table row in two, and one invoice's CER was 23%. It is 0.2% after the fix (ADR 0003).
- **Run time** is about 5 seconds per page, with the machine also busy with other work.

## How extraction is scored (Phase 4)

`doc-extract score` compares each field with its gold value using a rule for its type (ADR 0005): identifiers exactly, amounts as numbers, states by name or code, and names, addresses, and descriptions at fuzzy similarity of 90 or more. Line items are paired by best agreement before scoring. Each field counts as correct, missed, wrong (counts against precision and recall), hallucinated, or correctly null. Headline numbers carry 95% bootstrap intervals over documents.

Line items hold about 60% of gold values (792 of 1,330 on dev), so header and line-item F1 are reported separately.

## Rules baseline (Phase 4)

Regular expressions over the rebuilt page text, with no LLM (`--method rules`). It reads header fields and totals, and does not attempt addresses, buyer names, or line items. Measured on 2026-11-07.

```bash
.venv/bin/doc-extract extract --method rules --data data/scanned/medium --split dev
.venv/bin/doc-extract score --predictions eval/results/pred-rules-medium-dev-rules.jsonl --data data/scanned/medium
```

| Input | Micro F1 (95% interval) | Header F1 | Invoice no. | Seller GSTIN | Grand total | Median seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Clean dev (33) | 0.496 (0.460 to 0.541) | 0.899 | 1.000 | 1.000 | 1.000 | 0.01 |
| Clean test_layout (68) | 0.469 (0.442 to 0.501) | 0.899 | 1.000 | 1.000 | 1.000 | 0.01 |
| Medium scan dev (33) | 0.486 (0.452 to 0.529) | 0.880 | 0.818 | 1.000 | 1.000 | 5.3 |
| Heavy scan dev (33) | 0.136 (0.107 to 0.172) | 0.293 | 0.000 | 0.154 | 0.500 | 3.7 |

What this shows:

- **Every header field the rules attempt is right on clean text,** including the held-out layout, because the rules use generic labels rather than this generator's templates. Header F1 stays below 1 only because addresses and buyer names are not attempted. No document is exact, since no line items are extracted.
- **Rules depend entirely on OCR.** Medium scans lose the invoice numbers OCR misreads (F1 0.82) and a few states (seller 0.97, buyer 0.94); everything else holds. Heavy scans drop header F1 from 0.90 to 0.29.
- **The paired comparison catches small real differences.** Clean vs medium scans differ by +0.010 micro F1. Their separate intervals overlap almost completely, but the paired bootstrap gives +0.004 to +0.017, with the difference never at or below zero in 2,000 resamples.
- **The regression test found a real bug.** An invoice-number pattern allowed `\s*` to cross a line break, so "TAX INVOICE" followed by a seller named "Nova..." read as invoice number `va`.

## LLM extraction baseline (Phase 3)

One call per invoice to `gemini-3.5-flash-lite` (free tier), temperature 0, schema-constrained JSON. Measured on 2026-11-07. Saved model output was re-parsed with the final parser (`doc-extract reparse`), so every run is parsed the same way.

```bash
.venv/bin/doc-extract extract --method vision --data data/scanned/heavy --split dev
.venv/bin/doc-extract score --predictions eval/results/pred-vision-heavy-dev-gemini.jsonl --data data/scanned/heavy
```

| Method | Input (documents) | Parsed | Micro F1 (95% interval) | Header F1 | Line-item F1 | Exact | Mean input tokens | Median seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| text | clean dev (33) | 100% | 1.000 (1.000 to 1.000) | 1.000 | 1.000 | 100.0% | 603 | 3.1 |
| vision | clean dev (33) | 100% | 1.000 (1.000 to 1.000) | 1.000 | 1.000 | 100.0% | 1297 | 5.1 |
| text | held-out layout (68) | 100% | 0.997 (0.995 to 0.999) | 0.992 | 1.000 | 89.7% | 619 | 2.7 |
| vision | held-out layout (68) | 100% | 1.000 (1.000 to 1.000) | 1.000 | 1.000 | 100.0% | 1297 | 4.4 |
| text | heavy scan dev (33) | 100% | 0.440 (0.353 to 0.525) | 0.431 | 0.447 | 0.0% | 578 | 6.4 |
| vision | heavy scan dev (33) | 100% | 0.873 (0.837 to 0.905) | 0.801 | 0.922 | 9.1% | 1297 | 5.8 |

Output averaged about 670 tokens for every run. Text times include OCR on scans.

What this shows:

- **Clean synthetic invoices are solved.** Both methods are perfect on clean dev, so these invoices no longer separate methods; scans and unseen layouts do. This is the upper bound ADR 0002 warned about.
- **Vision wins on the held-out layout.** Text loses 7 of 68 invoices: six addresses whose PIN code wraps onto a second line, which the row-rebuilt text splits off, and one invoice where seller and buyer GSTINs were swapped. Vision gets all 68 right.
- **Vision wins by far on heavy scans.** Paired difference in micro F1 is +0.433 (95% interval +0.368 to +0.501). Text inherits OCR damage; vision reads the image directly. Still, only 3 of 33 heavy scans come out fully right. Identifiers suffer most: seller and buyer GSTINs reach F1 0.24 and 0.20, and invoice numbers 0.33, because a blurred character in a code has no context to recover from. Amounts sit between 0.72 and 0.87.
- **Vision costs about twice the input tokens** (a fixed 1,297 per page against about 600 for text) for the same output, and similar latency.
- **The first parser threw away whole invoices.** On heavy scans, the model copies OCR garbage such as `9870:46` into number fields, and one unreadable amount made the whole invoice fail to parse: 11 of 33 text predictions were lost. The parser now sets unreadable fields to null, and reads `1.072.40` (a comma misread as a point) as 1072.40.

## Validation, repair, and the agent (Phase 5)

Measured on 2026-11-07 on heavy scans (dev, 33 invoices) with `gemini-3.5-flash-lite`, scored on 2026-11-16.

```bash
.venv/bin/doc-extract score --predictions <run>.jsonl --data data/scanned/heavy --validation --vendors data/vendors.json
.venv/bin/doc-extract compare --baseline pred-vision-heavy-dev-gemini.jsonl --candidate repair-vision-vendors-heavy-dev.jsonl --data data/scanned/heavy
```

**Validation as a detector.** On single-call vision output, the checks flagged 29 of the 30 invoices that had any wrong field, with no false alarms on the 3 correct ones. The most common codes were an invalid GSTIN checksum (29), an unknown seller (25), and a wrong tax amount (15). On the held-out layout with the text method, rules alone flagged 0 of the 7 wrong invoices: six had an address whose PIN code wrapped onto a second line, and one had seller and buyer GSTINs swapped. All seven are internally consistent. The vendor master caught the swap; nothing automatic caught the addresses.

| Method | Micro F1 (95% interval) | Header F1 | Exact | Mean input tokens | Mean output tokens | Median seconds |
| --- | --- | --- | --- | --- | --- | --- |
| vision, one call (33) | 0.873 (0.837 to 0.905) | 0.801 | 9.1% | 1,297 | 671 | 5.8 |
| vision + repair, vendor hints (33) | 0.894 (0.857 to 0.929) | 0.844 | 21.2% | 4,543 | 1,637 | 13.1 |
| tool-calling agent (11 of 33 completed) | 0.884 on its 11 | 0.811 | 0.0% | about 38,300 | about 1,200 | 39 |

- **Repair is a real but modest gain.** Paired difference in micro F1 is +0.021 (95% interval +0.007 to +0.035), and exact invoices rise from 3 to 7. It fixed 51 correct values and broke 23, mostly round-off and taxable values: re-reading a blurred number is noisy, so a repair changes some right values too. It costs 3.5 times the input tokens and twice the latency.
- **The agent is not worth its cost so far.** On the 11 invoices it finished, it scored 0.884 against 0.893 for one vision call and 0.903 for repair on the same invoices, with no exact invoices, about 30 times the input tokens of one call, and about 7 times the latency. Its history grows every turn, because each `view_page` image is sent again with every later turn. Four of the 15 runs hit HTTP 429 rate limits, three of them before producing anything. The remaining 18 invoices are still to run, so this is provisional.
- **Validation after repair** still flagged 24 of the 26 wrong invoices with no false alarms, so repaired output can go through the same review policy.

## Confidence and review (Phase 6)

Field confidence comes from signals that need no gold label (ADR 0007): agreement between the vision and text extractions, failed checks, changes made during repair, and the vendor master. Measured on 2026-11-07.

```bash
.venv/bin/doc-extract calibrate --primary <vision or repair run> --secondary <text run> --data <dataset> --vendors data/vendors.json
.venv/bin/doc-extract review --primary <run> --secondary <run> --vendors data/vendors.json --calibration eval/calibration.json
```

Reliability on heavy scans (repaired vision, checked against text, 33 invoices):

| Raw level | Meaning | Fields | Actually right |
| --- | --- | --- | --- |
| 0.99 | vendor master confirms seller GSTIN | 5 | 100% |
| 0.98 | vision and text agree | 446 | 99.8% |
| 0.50 | changed during repair | 186 | 90.3% |
| 0.35 | vision and text disagree | 314 | 87.6% |
| 0.30 | a failed check names the field | 379 | 78.1% |

The levels rank fields correctly but the low ones are far too pessimistic, so raw expected calibration error is 0.32. Calibration fitted on one half of the heavy scans and tested on the other cuts it to 0.011 and 0.009 in the two directions. The fitted values are stable across halves: disagreement means 87 to 89% right, a failed check 78%.

Approval policy: approve only if no check fails and every field reaches 0.98.

| Documents | Approved without review | Approved with an error | Fields flagged for checking | Errors inside flagged fields |
| --- | --- | --- | --- | --- |
| Held-out layout, vision checked against text (68) | 61 (89.7%) | 0 | 0.3% | no errors to find |
| Heavy scans, repaired vision checked against text (33) | 0 | 0 | 66% | 140 of 141 (99.3%) |

What this shows:

- **Agreement between two methods is the most useful signal.** Fields where vision and text agree are right 99.8% of the time even on heavy scans.
- **The policy approves what it should and nothing else.** Nine in ten held-out-layout invoices skip review with no errors. All 7 invoices the text method got wrong were caught by disagreement or the vendor check, and vision had them right. The 7 invoices sent to review are exactly the 7 the text method got wrong. No heavy scan is approved, which is correct, since 26 of 33 still contain errors after repair.
- **Targeted review saves a third of the checking on bad scans** while showing reviewers 99.3% of the errors.
- **Raw levels need calibration before anyone reads them as probabilities.** Hand-picked numbers ranked well but were off by about 0.3.
