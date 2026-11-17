"""Turn clean PDFs into image-only "scans" with controlled damage.

Each severity is a range of skew, blur, fading, noise, resolution, and JPEG
quality. Scanned copies keep the original labels, so OCR and extraction can be
scored at each level of damage.
"""

import io
import json
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from doc_extract.ingestion.document import render_page


@dataclass(frozen=True)
class Severity:
    dpi: int
    max_skew_degrees: float
    blur_radius: tuple[float, float]
    fade: tuple[float, float]  # share of contrast lost toward grey paper
    noise_std: float
    jpeg_quality: tuple[int, int]


SEVERITIES = {
    "light": Severity(200, 0.7, (0.2, 0.5), (0.0, 0.1), 4, (70, 85)),
    "medium": Severity(150, 1.5, (0.5, 0.9), (0.1, 0.25), 10, (45, 65)),
    "heavy": Severity(100, 3.0, (0.8, 1.3), (0.25, 0.4), 18, (25, 40)),
}
PAPER_GREY = 235
FIXED_PDF_DATE = time.gmtime(0)


def simulate_scan(pdf: bytes, rng: random.Random, severity: Severity) -> bytes:
    image = render_page(pdf, 1, dpi=severity.dpi).convert("L")
    angle = rng.uniform(-severity.max_skew_degrees, severity.max_skew_degrees)
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(*severity.blur_radius)))

    pixels = np.asarray(image, dtype=np.float32)
    fade = rng.uniform(*severity.fade)
    pixels = pixels * (1 - fade) + PAPER_GREY * fade
    noise = np.random.default_rng(rng.getrandbits(32)).normal(0, severity.noise_std, pixels.shape)
    image = Image.fromarray(np.clip(pixels + noise, 0, 255).astype(np.uint8))

    # The PDF stores the page as a JPEG at this quality. Pillow stamps the current time
    # into the PDF unless given fixed dates, which would make every build differ.
    output = io.BytesIO()
    image.save(
        output,
        "PDF",
        resolution=severity.dpi,
        quality=rng.randint(*severity.jpeg_quality),
        creationDate=FIXED_PDF_DATE,
        modDate=FIXED_PDF_DATE,
    )
    return output.getvalue()


def build_scanned_dataset(src_dir: Path, out_dir: Path, severity_name: str, seed: str) -> int:
    severity = SEVERITIES[severity_name]
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in (src_dir / "manifest.jsonl").read_text().splitlines()]
    with (out_dir / "manifest.jsonl").open("w") as manifest:
        for record in records:
            doc_id = record["doc_id"]
            rng = random.Random(f"{seed}-{severity_name}-{doc_id}")
            scanned = simulate_scan((src_dir / f"{doc_id}.pdf").read_bytes(), rng, severity)
            (out_dir / f"{doc_id}.pdf").write_bytes(scanned)
            shutil.copyfile(src_dir / f"{doc_id}.json", out_dir / f"{doc_id}.json")
            manifest.write(json.dumps({**record, "scan": severity_name}) + "\n")
    return len(records)
