"""Batch runs for the repair pipeline and the agent, saved like baseline predictions.

Rows carry the same fields as baseline predictions, so `score` and `compare` work
on them unchanged, plus the repair rounds or the agent's tool trace.
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

ExtractOne = Callable[[bytes, str], dict]


def run_batch(
    data_dir: Path,
    split: str,
    extract_one: ExtractOne,
    out_path: Path,
    limit: int | None = None,
    on_row: Callable[[dict], None] | None = None,
) -> int:
    """Append one row per document to out_path, skipping documents already done."""
    records = [json.loads(line) for line in (data_dir / "manifest.jsonl").read_text().splitlines()]
    doc_ids = [record["doc_id"] for record in records if record["split"] == split][:limit]
    done = set()
    if out_path.exists():
        done = {json.loads(line)["doc_id"] for line in out_path.read_text().splitlines()}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("a") as out:
        for doc_id in doc_ids:
            if doc_id in done:
                continue
            row = extract_one((data_dir / f"{doc_id}.pdf").read_bytes(), doc_id)
            out.write(json.dumps(row) + "\n")
            out.flush()
            count += 1
            if on_row:
                on_row(row)
    return count


def repair_row(prediction, rounds) -> dict:
    return {**asdict(prediction), "rounds": [asdict(r) for r in rounds]}
