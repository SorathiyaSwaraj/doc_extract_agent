import json

from doc_extract.evaluation.report import load_run
from doc_extract.evaluation.validation_quality import validation_quality
from doc_extract.synthetic.dataset import build_dataset

TODAY_ISO = "2026-11-07"


def test_counts_caught_missed_and_false_alarms(tmp_path):
    build_dataset(tmp_path, count=4, seed="vq")
    rows = []
    for index, line in enumerate((tmp_path / "manifest.jsonl").read_text().splitlines()):
        doc_id = json.loads(line)["doc_id"]
        invoice = json.loads((tmp_path / f"{doc_id}.json").read_text())
        if index == 0:
            invoice["totals"]["grand_total"] = "1.00"  # wrong and inconsistent: caught
        elif index == 1:
            invoice["invoice_number"] = "WRONG"  # wrong but consistent: missed
        elif index == 2:
            invoice = None  # unparsed: caught
        rows.append(
            {
                "doc_id": doc_id,
                "invoice": invoice,
                "error": None if invoice else "invalid JSON",
                "provider": "p",
                "model": "m",
                "prompt_version": "v1",
                "input_tokens": None,
                "output_tokens": None,
                "total_seconds": 1.0,
            }
        )
    path = tmp_path / "p.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))

    result = validation_quality(load_run(path, tmp_path))

    assert (result["wrong"], result["caught"], result["missed"]) == (3, 2, 1)
    assert result["false_alarms"] == 0
    assert result["recall"] == round(2 / 3, 4)
    assert result["precision"] == 1
    assert result["codes_on_wrong_documents"] == {"grand_total": 1, "unparsed": 1}
