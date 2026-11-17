import json

from doc_extract.models import Invoice
from doc_extract.synthetic.dataset import HELD_OUT_LAYOUT, assign_split, build_dataset


def read_manifest(out_dir):
    return [json.loads(line) for line in (out_dir / "manifest.jsonl").read_text().splitlines()]


def test_every_document_has_pdf_label_and_manifest_row(tmp_path):
    splits = build_dataset(tmp_path, count=12, seed="t")
    records = read_manifest(tmp_path)

    assert sum(splits.values()) == 12
    assert [r["doc_id"] for r in records] == [f"syn-{i:05d}" for i in range(12)]
    for record in records:
        assert (tmp_path / f"{record['doc_id']}.pdf").read_bytes().startswith(b"%PDF")
        Invoice.model_validate_json((tmp_path / f"{record['doc_id']}.json").read_text())


def test_held_out_layout_appears_only_in_its_split(tmp_path):
    build_dataset(tmp_path, count=40, seed="t")

    for record in read_manifest(tmp_path):
        assert (record["layout"] == HELD_OUT_LAYOUT) == (record["split"] == "test_layout")


def test_same_seed_rebuilds_identical_files(tmp_path):
    build_dataset(tmp_path / "a", count=5, seed="t")
    build_dataset(tmp_path / "b", count=5, seed="t")

    for path in (tmp_path / "a").iterdir():
        assert path.read_bytes() == (tmp_path / "b" / path.name).read_bytes()


def test_adding_documents_keeps_existing_ones(tmp_path):
    build_dataset(tmp_path / "small", count=3, seed="t")
    build_dataset(tmp_path / "large", count=6, seed="t")

    assert read_manifest(tmp_path / "small") == read_manifest(tmp_path / "large")[:3]


def test_split_depends_only_on_id():
    assert assign_split("syn-00007", "classic") == assign_split("syn-00007", "compact")
