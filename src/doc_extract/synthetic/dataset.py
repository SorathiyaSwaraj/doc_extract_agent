"""Build a labelled synthetic dataset with fixed splits.

Each document gets its own seed, so any one document can be regenerated alone,
and adding documents never changes existing ones. The modern layout appears only
in the test_layout split: scores there show how well a method handles a layout
it was never tuned or trained on.
"""

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from doc_extract.synthetic.invoices import generate_invoice
from doc_extract.synthetic.render import choose_style, render_invoice

SEEN_LAYOUTS = ("classic", "compact")
HELD_OUT_LAYOUT = "modern"
HELD_OUT_SHARE = 0.2


def build_dataset(out_dir: Path, count: int, seed: str) -> Counter:
    out_dir.mkdir(parents=True, exist_ok=True)
    splits: Counter = Counter()
    with (out_dir / "manifest.jsonl").open("w") as manifest:
        for index in range(count):
            doc_id = f"syn-{index:05d}"
            doc_seed = f"{seed}-{index}"
            rng = random.Random(doc_seed)
            invoice = generate_invoice(rng)
            if rng.random() < HELD_OUT_SHARE:
                layout = HELD_OUT_LAYOUT
            else:
                layout = rng.choice(SEEN_LAYOUTS)
            style = choose_style(rng, layout)
            split = assign_split(doc_id, layout)

            (out_dir / f"{doc_id}.pdf").write_bytes(render_invoice(invoice, style))
            (out_dir / f"{doc_id}.json").write_text(invoice.model_dump_json(indent=2))
            record = {"doc_id": doc_id, "split": split, "layout": layout, "seed": doc_seed}
            manifest.write(json.dumps(record) + "\n")
            splits[split] += 1
    return splits


def assign_split(doc_id: str, layout: str) -> str:
    """Hash the id rather than drawing randomly, so a document's split never changes."""
    if layout == HELD_OUT_LAYOUT:
        return "test_layout"
    bucket = int(hashlib.sha256(doc_id.encode()).hexdigest(), 16) % 100
    if bucket < 70:
        return "train"
    return "dev" if bucket < 85 else "test"
