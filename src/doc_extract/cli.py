import argparse
import json
from pathlib import Path

from doc_extract.synthetic.dataset import build_dataset
from doc_extract.synthetic.scan import SEVERITIES, build_scanned_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doc-extract")
    commands = parser.add_subparsers(dest="command", required=True)

    synth = commands.add_parser("synth", help="Generate labelled synthetic invoices.")
    synth.add_argument("--count", type=int, default=300)
    synth.add_argument("--seed", default="v1", help="Same seed, same dataset.")
    synth.add_argument("--out", type=Path, default=Path("data/synthetic"))

    scan = commands.add_parser("scan", help="Make image-only scanned copies of a dataset.")
    scan.add_argument("--severity", choices=SEVERITIES, default="medium")
    scan.add_argument("--src", type=Path, default=Path("data/synthetic"))
    scan.add_argument("--out", type=Path, help="Default: data/scanned/<severity>")
    scan.add_argument("--seed", default="v1")

    ocr_eval = commands.add_parser("ocr-eval", help="Measure OCR against clean text layers.")
    ocr_eval.add_argument("--data", type=Path, required=True, help="Dataset to OCR.")
    ocr_eval.add_argument("--reference", type=Path, default=Path("data/synthetic"))
    ocr_eval.add_argument("--split", default="dev")
    ocr_eval.add_argument("--limit", type=int)
    ocr_eval.add_argument("--json", type=Path, help="Also write the summary here.")

    extract = commands.add_parser("extract", help="Run LLM extraction and save predictions.")
    extract.add_argument("--data", type=Path, default=Path("data/synthetic"))
    extract.add_argument("--split", default="dev")
    extract.add_argument("--method", choices=("text", "vision", "rules"), default="text")
    extract.add_argument("--provider", choices=("gemini", "ollama"), default="gemini")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--out", type=Path, help="Default: eval/results/pred-...jsonl")

    score = commands.add_parser("score", help="Score saved predictions field by field.")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--data", type=Path, default=Path("data/synthetic"))
    score.add_argument("--input-price", type=float, help="USD per million input tokens.")
    score.add_argument("--output-price", type=float, help="USD per million output tokens.")
    score.add_argument("--json", type=Path, help="Also write the full summary here.")
    score.add_argument("--fail-under", type=float, help="Exit 1 if micro F1 is below this.")

    score.add_argument(
        "--validation", action="store_true", help="Also report how well validation flags errors."
    )
    score.add_argument("--vendors", type=Path, help="Vendor master JSON for validation.")

    reparse = commands.add_parser("reparse", help="Re-parse saved model output, no LLM calls.")
    reparse.add_argument("predictions", type=Path, nargs="+")

    vendors = commands.add_parser("vendors", help="Build a vendor master from dataset labels.")
    vendors.add_argument("--data", type=Path, default=Path("data/synthetic"))
    vendors.add_argument("--out", type=Path, default=Path("data/vendors.json"))

    agent = commands.add_parser("agent", help="Run the repair pipeline or the tool agent.")
    agent.add_argument("--mode", choices=("repair", "tools"), required=True)
    agent.add_argument(
        "--method",
        choices=("text", "vision"),
        default="vision",
        help="First-pass input for repair mode.",
    )
    agent.add_argument("--data", type=Path, default=Path("data/synthetic"))
    agent.add_argument("--split", default="dev")
    agent.add_argument("--vendors", type=Path, help="Vendor master JSON.")
    agent.add_argument("--limit", type=int)
    agent.add_argument("--out", type=Path, help="Default: eval/results/<mode>-...jsonl")

    calibrate_cmd = commands.add_parser("calibrate", help="Fit confidence calibration on a run.")
    review = commands.add_parser("review", help="Build the approve-or-review queue for a run.")
    for sub in (calibrate_cmd, review):
        sub.add_argument("--primary", type=Path, required=True, help="Predictions to review.")
        sub.add_argument("--secondary", type=Path, help="Independent second extraction.")
        sub.add_argument("--vendors", type=Path, help="Vendor master JSON.")
    calibrate_cmd.add_argument("--data", type=Path, default=Path("data/synthetic"))
    calibrate_cmd.add_argument("--out", type=Path, default=Path("eval/calibration.json"))
    review.add_argument("--calibration", type=Path, help="Output of the calibrate command.")
    review.add_argument("--threshold", type=float, default=0.98)
    review.add_argument("--out", type=Path, default=Path("eval/results/review-queue.jsonl"))

    serve = commands.add_parser("serve", help="Run the HTTP service and its workers.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    mcp = commands.add_parser("mcp", help="Run the MCP server for AI assistants.")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")

    compare = commands.add_parser("compare", help="Paired comparison of two prediction runs.")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--data", type=Path, default=Path("data/synthetic"))

    args = parser.parse_args(argv)
    if args.command == "synth":
        splits = build_dataset(args.out, args.count, args.seed)
        summary = ", ".join(f"{name} {n}" for name, n in sorted(splits.items()))
        print(f"Wrote {args.count} invoices to {args.out} ({summary})")
    elif args.command == "scan":
        out = args.out or Path("data/scanned") / args.severity
        count = build_scanned_dataset(args.src, out, args.severity, args.seed)
        print(f"Wrote {count} {args.severity} scans to {out}")
    elif args.command == "ocr-eval":
        from doc_extract.evaluation.ocr_quality import evaluate_ocr
        from doc_extract.ingestion.ocr import RapidOcrEngine

        report = evaluate_ocr(args.data, args.reference, RapidOcrEngine(), args.split, args.limit)
        summary = report.summary()
        print(json.dumps(summary, indent=2))
        if args.json:
            args.json.write_text(json.dumps(summary, indent=2) + "\n")
    elif args.command == "extract":
        return _extract(args)
    elif args.command == "score":
        return _score(args)
    elif args.command == "reparse":
        from doc_extract.extraction.extractor import reparse_file

        for path in args.predictions:
            parsed, changed = reparse_file(path)
            print(f"{path}: re-parsed {parsed} rows, {changed} changed")
    elif args.command == "vendors":
        from doc_extract.validation.reference import build_vendor_master

        master = build_vendor_master(args.data)
        master.save(args.out)
        print(f"Wrote {len(master.vendors)} vendors to {args.out}")
    elif args.command == "agent":
        return _agent(args)
    elif args.command in ("calibrate", "review"):
        return _review(args)
    elif args.command == "serve":
        return _serve(args)
    elif args.command == "mcp":
        from doc_extract.mcp_server import main as run_mcp

        run_mcp(args.transport)
    elif args.command == "compare":
        from doc_extract.evaluation.report import (
            compare_runs,
            exact_rate,
            field_changes,
            load_run,
            micro_f1,
        )

        baseline = load_run(args.baseline, args.data)
        candidate = load_run(args.candidate, args.data)
        for name, metric in (("micro F1", micro_f1), ("exact documents", exact_rate)):
            result = compare_runs(baseline, candidate, metric)
            low, high = result["interval"]
            print(
                f"{name}: {result['difference']:+.4f} (95% interval {low:+.4f} to {high:+.4f}, "
                f"not better in {result['not_better_share']:.1%} of resamples, "
                f"{result['documents']} documents)"
            )
        changes = field_changes(baseline, candidate)
        print(
            f"correct values: {changes['fixed']} fixed, {changes['broken']} broken; "
            f"broken by field {changes['broken_by_field']}"
        )
    return 0


def _score(args: argparse.Namespace) -> int:
    from doc_extract.evaluation.report import load_run, summarise, to_markdown

    summary = summarise(load_run(args.predictions, args.data), args.input_price, args.output_price)
    print(to_markdown(summary))
    if args.validation:
        from doc_extract.evaluation.validation_quality import validation_quality
        from doc_extract.validation.reference import VendorMaster

        vendors = VendorMaster.load(args.vendors) if args.vendors else None
        quality = validation_quality(load_run(args.predictions, args.data), vendors=vendors)
        summary["validation"] = quality
        print(
            f"\nValidation: flagged {quality['caught']} of {quality['wrong']} wrong documents, "
            f"{quality['false_alarms']} false alarms; codes {quality['codes_on_wrong_documents']}"
        )
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n")
    if args.fail_under is not None and summary["micro"]["f1"] < args.fail_under:
        print(f"FAIL: micro F1 {summary['micro']['f1']:.4f} is below {args.fail_under}")
        return 1
    return 0


def _extract(args: argparse.Namespace) -> int:
    from doc_extract.extraction.extractor import run_extraction
    from doc_extract.extraction.llm import LLMError, build_provider
    from doc_extract.ingestion.ocr import RapidOcrEngine

    provider = None
    if args.method != "rules":
        try:
            provider = build_provider(args.provider)
        except LLMError as error:
            print(f"error: {error}")
            return 2
    source = provider.name if provider else "rules"
    name = f"pred-{args.method}-{args.data.name}-{args.split}-{source}.jsonl"
    out = args.out or Path("eval/results") / name
    ocr = RapidOcrEngine() if args.method in ("text", "rules") else None

    def report(prediction):
        status = f"error: {prediction.error}" if prediction.error else "ok"
        print(f"{prediction.doc_id}  {prediction.total_seconds:6.1f}s  {status}", flush=True)

    count = run_extraction(
        args.data, args.split, args.method, provider, out, args.limit, ocr, report
    )
    print(f"Wrote {count} new predictions to {out}")
    return 0


def _agent(args: argparse.Namespace) -> int:
    import os

    from doc_extract.agent.chat import GeminiChat
    from doc_extract.agent.loop import prediction_row, run_agent
    from doc_extract.agent.repair import extract_with_repair
    from doc_extract.agent.run import repair_row, run_batch
    from doc_extract.extraction.llm import LLMError, build_provider
    from doc_extract.ingestion.ocr import RapidOcrEngine
    from doc_extract.validation.reference import VendorMaster

    try:
        provider = build_provider("gemini")
    except LLMError as error:
        print(f"error: {error}")
        return 2
    vendors = VendorMaster.load(args.vendors) if args.vendors else None
    ocr = RapidOcrEngine()
    with_vendors = "-vendors" if vendors else ""
    if args.mode == "repair":
        label = f"repair-{args.method}{with_vendors}"

        def extract_one(pdf: bytes, doc_id: str) -> dict:
            return repair_row(
                *extract_with_repair(pdf, doc_id, args.method, provider, ocr, vendors)
            )
    else:
        label = f"agent{with_vendors}"
        chat = GeminiChat(provider.api_key, os.environ.get("GEMINI_MODEL", provider.model))

        def extract_one(pdf: bytes, doc_id: str) -> dict:
            return prediction_row(*run_agent(pdf, doc_id, chat, ocr, vendors))

    out = args.out or Path("eval/results") / f"{label}-{args.data.name}-{args.split}.jsonl"

    def report(row: dict) -> None:
        status = f"error: {row['error']}" if row["error"] else "ok"
        detail = row.get("trace") or [r["issues"] for r in row.get("rounds", [])]
        print(f"{row['doc_id']}  {row['total_seconds']:6.1f}s  {status}  {detail}", flush=True)

    count = run_batch(args.data, args.split, extract_one, out, args.limit, report)
    print(f"Wrote {count} new rows to {out}")
    return 0


def _review(args: argparse.Namespace) -> int:
    from doc_extract.evaluation.confidence_quality import confidence_quality
    from doc_extract.review.confidence import fit_calibration
    from doc_extract.review.queue import build_queue, save_queue
    from doc_extract.validation.reference import VendorMaster

    vendors = VendorMaster.load(args.vendors) if args.vendors else None
    if args.command == "calibrate":
        quality = confidence_quality(args.primary, args.data, args.secondary, vendors)
        mapping = fit_calibration(quality["reliability"])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({str(k): v for k, v in mapping.items()}, indent=2) + "\n")
        for item in quality["reliability"]:
            print(
                f"raw {item['confidence']:.2f} -> {item['accuracy']:.4f} ({item['fields']} fields)"
            )
        print(f"Wrote calibration to {args.out}")
        return 0
    calibration = None
    if args.calibration:
        calibration = {float(k): v for k, v in json.loads(args.calibration.read_text()).items()}
    queue = build_queue(args.primary, args.secondary, vendors, calibration, args.threshold)
    save_queue(queue, args.out)
    reviewed = [item for item in queue if item.decision == "review"]
    checks = sum(len(item.fields_to_check) for item in reviewed)
    print(
        f"{len(queue) - len(reviewed)} approved, {len(reviewed)} to review, "
        f"{checks} fields flagged for checking. Queue written to {args.out}"
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from doc_extract.service.config import ConfigError, Settings

    try:
        Settings.from_env()  # fail here, with a readable message, rather than in uvicorn
    except ConfigError as error:
        print(f"error: {error}")
        return 2
    uvicorn.run(
        "doc_extract.service.api:app_from_env", factory=True, host=args.host, port=args.port
    )
    return 0
