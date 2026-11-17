# ADR 0003: Text layer first, RapidOCR for images, skew from box angles

**Status:** Accepted

**Context:** Invoices arrive as digital PDFs with a text layer or as scans with none. Extraction needs text with positions, so labels can be paired with values. The budget is zero, the machine has a CPU and no GPU, and installing system packages needs `sudo`.

**Options:**

- OCR: Tesseract (system package, weaker on noisy scans); RapidOCR (PaddleOCR models on ONNX Runtime, pip only, models ship in the package); PaddleOCR itself (large framework); cloud OCR (cost, and documents leave the machine).
- Skew: deskew the image and OCR it again; correct row grouping using the angles OCR already reports.

**Decision:**

- Use the PDF text layer when a page has at least 30 visible characters. Otherwise render at 200 DPI and run RapidOCR.
- Both paths produce the same `Line(text, box, score, angle)`, with boxes in PDF points from the top-left. Each page records its source, because OCR text deserves less trust.
- Rebuild rows by vertical overlap, after correcting for page skew. The skew is the median angle of long OCR boxes.
- Test OCR on scans simulated from the clean dataset at three severities, with labels unchanged. Scans are seeded and byte-for-byte reproducible.

**Consequences:** No OCR runs on digital PDFs, which are fast and exact. OCR takes several seconds per page on CPU. A text layer is trusted without checking, so a PDF with a broken font mapping that yields garbage text would not fall back to OCR. Simulated damage is uniform across the page, unlike real stamps, folds, and handwriting. Grouping on skew-corrected heights assumes one skew per page, which fails on photographed pages with perspective distortion.
