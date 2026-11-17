import json

import pytest

from doc_extract.evaluation.report import (
    bootstrap_interval,
    compare_runs,
    exact_rate,
    field_changes,
    load_run,
    micro_f1,
    summarise,
    to_markdown,
)
from doc_extract.synthetic.dataset import build_dataset


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    path = tmp_path_factory.mktemp("data")
    build_dataset(path, count=30, seed="t")
    return path


def write_run(path, dataset, damage=lambda doc_id, invoice: invoice):
    """Predictions equal to gold, passed through `damage` to inject errors."""
    lines = []
    for line in (dataset / "manifest.jsonl").read_text().splitlines():
        doc_id = json.loads(line)["doc_id"]
        invoice = json.loads((dataset / f"{doc_id}.json").read_text())
        row = {
            "doc_id": doc_id,
            "method": "text",
            "prompt_version": "v1",
            "provider": "p",
            "model": "m",
            "invoice": damage(doc_id, invoice),
            "error": None,
            "input_tokens": 2000,
            "output_tokens": 500,
            "total_seconds": 2.0,
        }
        if row["invoice"] is None:
            row["error"] = "invalid JSON: x"
        lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + "\n")
    return path


def break_every_third(doc_id, invoice):
    if int(doc_id[-2:]) % 3 == 0:
        invoice["totals"]["grand_total"] = "1.00"
    return invoice


def test_perfect_run(dataset, tmp_path):
    run = load_run(write_run(tmp_path / "p.jsonl", dataset), dataset)

    summary = summarise(run, input_price=0.30, output_price=2.50)

    assert summary["micro"]["f1"] == 1
    assert summary["micro_f1_interval"] == (1, 1)
    assert summary["exact_rate"] == 1
    assert summary["cost_per_document"] == pytest.approx((2000 * 0.30 + 500 * 2.50) / 1e6)
    assert set(summary["by_layout"]) <= {"classic", "compact", "modern"}
    assert "| totals.grand_total | 1.000 | 1.000 | 1.000 |" in to_markdown(summary)
    assert summary["header"]["f1"] == summary["line_items"]["f1"] == 1
    assert summary["fields"]["totals.cess"]["f1"] is None
    assert "| totals.cess | n/a | n/a | n/a | 0 |" in to_markdown(summary)


def test_errors_lower_exact_rate_and_one_field(dataset, tmp_path):
    run = load_run(write_run(tmp_path / "p.jsonl", dataset, break_every_third), dataset)

    summary = summarise(run)

    assert summary["exact_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert summary["fields"]["totals.grand_total"]["f1"] < 1
    assert summary["fields"]["invoice_number"]["f1"] == 1
    low, high = summary["exact_rate_interval"]
    assert low < summary["exact_rate"] < high


def test_failed_parses_count_as_misses(dataset, tmp_path):
    run = load_run(write_run(tmp_path / "p.jsonl", dataset, lambda d, i: None), dataset)

    summary = summarise(run)

    assert summary["parsed"] == 0
    assert summary["micro"]["recall"] == 0
    assert summary["errors"] == ["invalid JSON"]


def test_interval_narrows_with_more_documents(dataset, tmp_path):
    run = load_run(write_run(tmp_path / "p.jsonl", dataset, break_every_third), dataset)

    low_few, high_few = bootstrap_interval(run.scores[:9], exact_rate)
    low_all, high_all = bootstrap_interval(run.scores, exact_rate)

    assert high_all - low_all < high_few - low_few


def test_paired_comparison_detects_a_real_gain(dataset, tmp_path):
    baseline = load_run(write_run(tmp_path / "a.jsonl", dataset, break_every_third), dataset)
    candidate = load_run(write_run(tmp_path / "b.jsonl", dataset), dataset)

    result = compare_runs(baseline, candidate, exact_rate)

    assert result["documents"] == 30
    assert result["difference"] == pytest.approx(1 / 3, abs=0.01)
    assert result["interval"][0] > 0
    assert result["not_better_share"] == 0


def test_identical_runs_show_no_gain(dataset, tmp_path):
    run = load_run(write_run(tmp_path / "a.jsonl", dataset, break_every_third), dataset)

    result = compare_runs(run, run, micro_f1)

    assert result["difference"] == 0
    assert result["not_better_share"] == 1


def test_field_changes_count_fixes_and_breakages(dataset, tmp_path):
    def break_invoice_number(doc_id, invoice):
        invoice["invoice_number"] = "WRONG"
        return invoice

    good = load_run(write_run(tmp_path / "good.jsonl", dataset), dataset)
    broken_numbers = load_run(
        write_run(tmp_path / "bad.jsonl", dataset, break_invoice_number), dataset
    )
    totals_broken = load_run(write_run(tmp_path / "t.jsonl", dataset, break_every_third), dataset)

    worse = field_changes(good, broken_numbers)
    mixed = field_changes(totals_broken, broken_numbers)

    assert (worse["fixed"], worse["broken"]) == (0, 30)
    assert worse["broken_by_field"] == {"invoice_number": 30}
    assert mixed["fixed_by_field"] == {"totals.grand_total": 10}
    assert mixed["broken_by_field"] == {"invoice_number": 30}
