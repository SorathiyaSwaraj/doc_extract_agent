"""Score a saved prediction run, with uncertainty, cost, and comparisons.

The dev split has about 30 documents, so a two-point change in F1 can be noise.
Every headline number gets a bootstrap interval: resample the documents with
replacement many times and see how much the metric moves. Comparisons use a
paired bootstrap over the same documents, which is far more sensitive than
comparing two separate intervals.
"""

import json
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from doc_extract.evaluation.scoring import Counts, DocumentScore, aggregate, score_document
from doc_extract.models import Invoice

ITERATIONS = 2000


@dataclass
class Run:
    rows: list[dict]
    scores: list[DocumentScore]
    groups: dict[str, str]  # doc_id -> layout


def load_run(predictions_path: Path, data_dir: Path) -> Run:
    rows = [json.loads(line) for line in predictions_path.read_text().splitlines()]
    manifest = {
        record["doc_id"]: record
        for record in map(json.loads, (data_dir / "manifest.jsonl").read_text().splitlines())
    }
    scores = []
    for row in rows:
        gold = Invoice.model_validate_json((data_dir / f"{row['doc_id']}.json").read_text())
        predicted = Invoice.model_validate(row["invoice"]) if row["invoice"] else None
        scores.append(score_document(row["doc_id"], predicted, gold))
    groups = {row["doc_id"]: manifest[row["doc_id"]]["layout"] for row in rows}
    return Run(rows, scores, groups)


def micro_f1(scores: Sequence[DocumentScore]) -> float:
    total = Counts()
    for score in scores:
        total.add(score.total)
    return total.f1


def exact_rate(scores: Sequence[DocumentScore]) -> float:
    return sum(score.exact for score in scores) / len(scores) if scores else 0.0


def bootstrap_interval(
    scores: Sequence[DocumentScore],
    metric: Callable[[Sequence[DocumentScore]], float],
    iterations: int = ITERATIONS,
    seed: int = 0,
) -> tuple[float, float]:
    """95% percentile interval of the metric over resampled document sets."""
    rng = random.Random(seed)
    values = sorted(metric(rng.choices(scores, k=len(scores))) for _ in range(iterations))
    return values[int(0.025 * iterations)], values[int(0.975 * iterations) - 1]


def compare_runs(
    baseline: Run,
    candidate: Run,
    metric: Callable[[Sequence[DocumentScore]], float],
    iterations: int = ITERATIONS,
    seed: int = 0,
) -> dict:
    """Paired bootstrap on documents present in both runs."""
    base = {score.doc_id: score for score in baseline.scores}
    cand = {score.doc_id: score for score in candidate.scores}
    shared = sorted(base.keys() & cand.keys())
    rng = random.Random(seed)
    differences = []
    for _ in range(iterations):
        sample = rng.choices(shared, k=len(shared))
        differences.append(metric([cand[i] for i in sample]) - metric([base[i] for i in sample]))
    differences.sort()
    observed = metric([cand[i] for i in shared]) - metric([base[i] for i in shared])
    return {
        "documents": len(shared),
        "difference": round(observed, 4),
        "interval": (
            round(differences[int(0.025 * iterations)], 4),
            round(differences[int(0.975 * iterations) - 1], 4),
        ),
        # Share of resamples where the candidate was not better: small means a real gain.
        "not_better_share": round(sum(d <= 0 for d in differences) / iterations, 4),
    }


def summarise(
    run: Run,
    input_price: float | None = None,
    output_price: float | None = None,
) -> dict:
    """Prices are per million tokens, passed in because they change too often to hard-code."""
    total = aggregate(run.scores)
    seconds = sorted(row["total_seconds"] for row in run.rows if row["total_seconds"] is not None)
    by_layout: dict[str, list[DocumentScore]] = defaultdict(list)
    for score in run.scores:
        by_layout[run.groups[score.doc_id]].append(score)

    summary = {
        "documents": total.documents,
        "models": sorted({f"{row['provider']}/{row['model']}" for row in run.rows}),
        "prompt_versions": sorted({row["prompt_version"] for row in run.rows}),
        "parsed": _ratio(total.parsed, total.documents),
        "micro": _metrics(total.total),
        # Line items hold most gold values, so one micro F1 can hide a perfect header.
        "header": _metrics(_sum(c for n, c in total.fields.items() if not n.startswith("line_"))),
        "line_items": _metrics(_sum(c for n, c in total.fields.items() if n.startswith("line_"))),
        "micro_f1_interval": _rounded(bootstrap_interval(run.scores, micro_f1)),
        "exact_rate": round(total.exact_rate, 4),
        "exact_rate_interval": _rounded(bootstrap_interval(run.scores, exact_rate)),
        "outcomes": dict(total.outcomes),
        "fields": {name: _metrics(counts) for name, counts in sorted(total.fields.items())},
        "by_layout": {
            layout: {"documents": len(group), "micro_f1": round(micro_f1(group), 4)}
            for layout, group in sorted(by_layout.items())
        },
        "seconds_p50": _percentile(seconds, 0.5),
        "seconds_p95": _percentile(seconds, 0.95),
        "mean_input_tokens": _mean(run.rows, "input_tokens"),
        "mean_output_tokens": _mean(run.rows, "output_tokens"),
        "errors": sorted({row["error"].split(":")[0] for row in run.rows if row["error"]}),
    }
    if input_price is not None and output_price is not None:
        costs = [
            ((row["input_tokens"] or 0) * input_price + (row["output_tokens"] or 0) * output_price)
            / 1_000_000
            for row in run.rows
        ]
        summary["cost_per_document"] = round(statistics.fmean(costs), 6)
    return summary


def to_markdown(summary: dict) -> str:
    low, high = summary["micro_f1_interval"]
    exact_low, exact_high = summary["exact_rate_interval"]
    lines = [
        f"Documents: {summary['documents']}, parsed {summary['parsed']:.1%}, "
        f"models {', '.join(summary['models'])}",
        f"Micro F1: {summary['micro']['f1']:.3f} (95% interval {low:.3f} to {high:.3f}); "
        f"header {summary['header']['f1']:.3f}, line items {summary['line_items']['f1']:.3f}",
        f"Exact documents: {summary['exact_rate']:.1%} "
        f"(95% interval {exact_low:.1%} to {exact_high:.1%})",
        "",
        "| Field | Precision | Recall | F1 | Gold values |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, metrics in summary["fields"].items():
        if metrics["f1"] is None:
            lines.append(f"| {name} | n/a | n/a | n/a | 0 |")
            continue
        lines.append(
            f"| {name} | {metrics['precision']:.3f} | {metrics['recall']:.3f} "
            f"| {metrics['f1']:.3f} | {metrics['support']} |"
        )
    return "\n".join(lines)


def _sum(counts_list) -> Counts:
    total = Counts()
    for counts in counts_list:
        total.add(counts)
    return total


def _metrics(counts: Counts) -> dict:
    """Metrics are None when there is nothing to score: no gold values and no predictions."""
    if counts.tp == counts.fp == counts.fn == 0:
        return {"precision": None, "recall": None, "f1": None, "support": 0}
    return {
        "precision": round(counts.precision, 4),
        "recall": round(counts.recall, 4),
        "f1": round(counts.f1, 4),
        "support": counts.tp + counts.fn,
    }


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _rounded(pair: tuple[float, float]) -> tuple[float, float]:
    return round(pair[0], 4), round(pair[1], 4)


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    return round(values[min(len(values) - 1, int(share * len(values)))], 2)


def _mean(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if row[key] is not None]
    return round(statistics.fmean(values), 1) if values else None


def field_changes(baseline: Run, candidate: Run) -> dict:
    """Correct values gained and lost, field by field, on documents in both runs.

    A net F1 gain can hide many correct values that a repair step broke. Counted
    per document and field, from correct-value counts, so line items work too.
    """
    base = {score.doc_id: score for score in baseline.scores}
    fixed: dict[str, int] = defaultdict(int)
    broken: dict[str, int] = defaultdict(int)
    for score in candidate.scores:
        before = base.get(score.doc_id)
        if before is None:
            continue
        for name in before.fields.keys() | score.fields.keys():
            difference = score.fields[name].tp - before.fields[name].tp
            if difference > 0:
                fixed[name] += difference
            elif difference < 0:
                broken[name] -= difference
    return {
        "fixed": sum(fixed.values()),
        "broken": sum(broken.values()),
        "fixed_by_field": dict(sorted(fixed.items(), key=lambda item: -item[1])),
        "broken_by_field": dict(sorted(broken.items(), key=lambda item: -item[1])),
    }
