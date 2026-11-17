import random

from doc_extract.synthetic.dataset import build_dataset
from doc_extract.synthetic.invoices import generate_invoice
from doc_extract.validation.reference import (
    DuplicateIndex,
    VendorMaster,
    build_vendor_master,
    check_reference,
)

INVOICE = generate_invoice(random.Random("reference"))
while INVOICE.buyer.gstin is None:
    INVOICE = generate_invoice(random.Random(f"reference-{INVOICE.invoice_number}"))
MASTER = VendorMaster({INVOICE.seller.gstin: INVOICE.seller.name})


def codes(issues):
    return {issue.code for issue in issues}


def test_known_seller_passes():
    assert check_reference(INVOICE, MASTER) == []


def test_swapped_gstins_are_caught():
    swapped = INVOICE.model_copy(
        update={
            "seller": INVOICE.seller.model_copy(update={"gstin": INVOICE.buyer.gstin}),
            "buyer": INVOICE.buyer.model_copy(update={"gstin": INVOICE.seller.gstin}),
        }
    )

    assert codes(check_reference(swapped, MASTER)) == {"vendor_unknown"}


def test_name_mismatch_is_a_warning():
    renamed = INVOICE.model_copy(
        update={"seller": INVOICE.seller.model_copy(update={"name": "Completely Different Co"})}
    )

    issues = check_reference(renamed, MASTER)

    assert codes(issues) == {"vendor_name_mismatch"}
    assert issues[0].severity == "warning"


def test_lookup_by_gstin_then_by_name():
    assert MASTER.lookup(gstin=INVOICE.seller.gstin)[0]["match"] == "gstin"
    by_name = MASTER.lookup(name=INVOICE.seller.name.upper())
    assert by_name[0]["gstin"] == INVOICE.seller.gstin
    assert MASTER.lookup(name="Nothing Like It") == []


def test_duplicates_are_caught_but_not_against_themselves():
    index = DuplicateIndex()
    index.add(INVOICE, "doc-1")

    assert check_reference(INVOICE, duplicates=index, doc_id="doc-1") == []
    respaced = INVOICE.model_copy(update={"invoice_number": f" {INVOICE.invoice_number} "})
    assert codes(check_reference(respaced, duplicates=index, doc_id="doc-2")) == {"duplicate"}


def test_vendor_master_round_trip(tmp_path):
    build_dataset(tmp_path / "data", count=5, seed="vm")
    master = build_vendor_master(tmp_path / "data")
    master.save(tmp_path / "vendors.json")

    assert VendorMaster.load(tmp_path / "vendors.json") == master
    assert len(master.vendors) == 5
