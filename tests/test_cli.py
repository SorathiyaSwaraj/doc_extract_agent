import json

from doc_extract.cli import main


def test_synth_then_scan(tmp_path, capsys):
    clean, scanned = tmp_path / "clean", tmp_path / "scanned"

    assert main(["synth", "--count", "3", "--out", str(clean)]) == 0
    assert main(["scan", "--severity", "light", "--src", str(clean), "--out", str(scanned)]) == 0

    output = capsys.readouterr().out
    assert "Wrote 3 invoices" in output
    assert "Wrote 3 light scans" in output
    rows = [json.loads(line) for line in (scanned / "manifest.jsonl").read_text().splitlines()]
    assert {row["scan"] for row in rows} == {"light"}


def test_extract_without_key_explains_how_to_get_one(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert main(["extract", "--data", str(tmp_path)]) == 2

    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_score_and_compare(tmp_path, capsys):
    data = tmp_path / "data"
    rules = tmp_path / "rules.jsonl"
    main(["synth", "--count", "12", "--out", str(data)])
    for split in ("train", "dev", "test", "test_layout"):
        main(
            [
                "extract",
                "--method",
                "rules",
                "--data",
                str(data),
                "--split",
                split,
                "--out",
                str(rules),
            ]
        )
    capsys.readouterr()

    assert main(["score", "--predictions", str(rules), "--data", str(data)]) == 0
    assert "| invoice_number | 1.000 | 1.000 | 1.000 |" in capsys.readouterr().out
    assert (
        main(["score", "--predictions", str(rules), "--data", str(data), "--fail-under", "0.99"])
        == 1
    )
    assert (
        main(["compare", "--baseline", str(rules), "--candidate", str(rules), "--data", str(data)])
        == 0
    )
    assert "micro F1: +0.0000" in capsys.readouterr().out


def test_reparse_and_vendors(tmp_path, capsys):
    data = tmp_path / "data"
    main(["synth", "--count", "3", "--out", str(data)])
    doc_id = json.loads((data / "manifest.jsonl").read_text().splitlines()[0])["doc_id"]
    raw = json.loads((data / f"{doc_id}.json").read_text())
    raw["totals"]["sgst"] = "9870:46"
    predictions = tmp_path / "p.jsonl"
    predictions.write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "raw": json.dumps(raw),
                "invoice": None,
                "error": "schema mismatch: old parser",
            }
        )
        + "\n"
    )

    assert main(["reparse", str(predictions)]) == 0
    assert main(["vendors", "--data", str(data), "--out", str(tmp_path / "v.json")]) == 0

    row = json.loads(predictions.read_text())
    assert row["error"] is None and row["dropped_fields"] == ["totals.sgst"]
    output = capsys.readouterr().out
    assert "re-parsed 1 rows, 1 changed" in output and "Wrote 3 vendors" in output


def test_calibrate_and_review(tmp_path, capsys):
    data = tmp_path / "data"
    main(["synth", "--count", "6", "--out", str(data)])
    rows = []
    for line in (data / "manifest.jsonl").read_text().splitlines():
        doc_id = json.loads(line)["doc_id"]
        rows.append(
            {"doc_id": doc_id, "invoice": json.loads((data / f"{doc_id}.json").read_text())}
        )
    run = tmp_path / "run.jsonl"
    run.write_text("".join(json.dumps(row) + "\n" for row in rows))
    calibration = tmp_path / "cal.json"

    assert (
        main(
            [
                "calibrate",
                "--primary",
                str(run),
                "--secondary",
                str(run),
                "--data",
                str(data),
                "--out",
                str(calibration),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "review",
                "--primary",
                str(run),
                "--secondary",
                str(run),
                "--calibration",
                str(calibration),
                "--out",
                str(tmp_path / "q.jsonl"),
            ]
        )
        == 0
    )

    assert json.loads(calibration.read_text()) == {"0.98": 1.0}
    assert "6 approved, 0 to review" in capsys.readouterr().out
