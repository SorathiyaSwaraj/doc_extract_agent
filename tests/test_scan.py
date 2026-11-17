import json
import random
import time

import numpy as np
import pytest

from doc_extract.ingestion.document import load_pdf, render_page
from doc_extract.synthetic.dataset import build_dataset
from doc_extract.synthetic.scan import SEVERITIES, build_scanned_dataset, simulate_scan


def damage(clean_pdf, scanned_pdf):
    """Mean pixel difference from the clean page, at the scan's resolution."""
    scanned = render_page(scanned_pdf, 1, dpi=100).convert("L")
    clean = render_page(clean_pdf, 1, dpi=100).convert("L").resize(scanned.size)
    return np.abs(np.asarray(scanned, float) - np.asarray(clean, float)).mean()


@pytest.mark.parametrize("name", SEVERITIES)
def test_scans_are_image_only_and_deterministic(sample_invoice, name):
    _, pdf = sample_invoice

    first = simulate_scan(pdf, random.Random("s"), SEVERITIES[name])
    second = simulate_scan(pdf, random.Random("s"), SEVERITIES[name])

    assert first == second
    assert load_pdf(first).pages[0].source == "ocr"


def test_scans_do_not_depend_on_the_clock(sample_invoice):
    """Pillow writes the save time into PDFs; two saves a second apart must still match."""
    _, pdf = sample_invoice

    first = simulate_scan(pdf, random.Random("s"), SEVERITIES["light"])
    time.sleep(1.1)

    assert simulate_scan(pdf, random.Random("s"), SEVERITIES["light"]) == first


def test_heavier_severity_damages_more(sample_invoice):
    _, pdf = sample_invoice
    scores = [
        damage(pdf, simulate_scan(pdf, random.Random("s"), SEVERITIES[name]))
        for name in ("light", "medium", "heavy")
    ]

    assert scores == sorted(scores)


def test_scanned_dataset_keeps_labels_and_splits(tmp_path):
    build_dataset(tmp_path / "clean", count=3, seed="t")

    count = build_scanned_dataset(tmp_path / "clean", tmp_path / "scan", "light", seed="t")

    assert count == 3
    clean = (tmp_path / "clean" / "manifest.jsonl").read_text().splitlines()
    scanned = (tmp_path / "scan" / "manifest.jsonl").read_text().splitlines()
    for before, after in zip(clean, scanned, strict=True):
        assert json.loads(after) == {**json.loads(before), "scan": "light"}
        doc_id = json.loads(before)["doc_id"]
        label = (tmp_path / "clean" / f"{doc_id}.json").read_bytes()
        assert (tmp_path / "scan" / f"{doc_id}.json").read_bytes() == label
