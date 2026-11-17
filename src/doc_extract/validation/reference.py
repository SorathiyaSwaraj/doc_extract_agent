"""Checks against data the business already holds: its vendor master and the
invoices it has already processed.

These catch errors that are internally consistent and so pass every arithmetic
check: seller and buyer GSTINs swapped, or the same invoice submitted twice.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from doc_extract.models import Invoice
from doc_extract.validation.checks import Issue

NAME_MATCH = 85


@dataclass
class VendorMaster:
    """Known suppliers by GSTIN. In a company this comes from the ERP."""

    vendors: dict[str, str] = field(default_factory=dict)  # GSTIN -> legal name

    @classmethod
    def load(cls, path: Path) -> "VendorMaster":
        return cls(json.loads(path.read_text()))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.vendors, indent=2, sort_keys=True) + "\n")

    def lookup(self, gstin: str | None = None, name: str | None = None) -> list[dict]:
        """Exact GSTIN match first; otherwise vendors whose names are close."""
        if gstin and gstin in self.vendors:
            return [{"gstin": gstin, "name": self.vendors[gstin], "match": "gstin"}]
        if not name:
            return []
        scored = sorted(
            (
                (fuzz.token_sort_ratio(name.casefold(), known.casefold()), g, known)
                for g, known in self.vendors.items()
            ),
            reverse=True,
        )
        return [
            {"gstin": g, "name": known, "match": f"name {score:.0f}"}
            for score, g, known in scored[:3]
            if score >= NAME_MATCH
        ]


@dataclass
class DuplicateIndex:
    """Invoices already accepted, keyed by seller GSTIN and invoice number."""

    seen: dict[str, str] = field(default_factory=dict)  # key -> doc_id

    @staticmethod
    def key(invoice: Invoice) -> str | None:
        if not invoice.seller.gstin or not invoice.invoice_number:
            return None
        return f"{invoice.seller.gstin.upper()}|{''.join(invoice.invoice_number.split())}"

    def add(self, invoice: Invoice, doc_id: str) -> None:
        key = self.key(invoice)
        if key:
            self.seen.setdefault(key, doc_id)


def check_reference(
    invoice: Invoice,
    vendors: VendorMaster | None = None,
    duplicates: DuplicateIndex | None = None,
    doc_id: str | None = None,
) -> list[Issue]:
    issues = []
    seller = invoice.seller
    if vendors is not None and seller.gstin:
        known = vendors.vendors.get(seller.gstin)
        if known is None:
            issues.append(
                Issue(
                    "vendor_unknown",
                    "error",
                    ("seller.gstin",),
                    f"seller GSTIN {seller.gstin} is not in the vendor master; "
                    "it may belong to the buyer or be misread",
                )
            )
        elif (
            seller.name
            and fuzz.token_sort_ratio(seller.name.casefold(), known.casefold()) < NAME_MATCH
        ):
            issues.append(
                Issue(
                    "vendor_name_mismatch",
                    "warning",
                    ("seller.name", "seller.gstin"),
                    f"vendor master has {known!r} for {seller.gstin}, not {seller.name!r}",
                )
            )
    if duplicates is not None:
        key = DuplicateIndex.key(invoice)
        first = duplicates.seen.get(key) if key else None
        if first is not None and first != doc_id:
            issues.append(
                Issue(
                    "duplicate",
                    "error",
                    ("invoice_number", "seller.gstin"),
                    f"same seller and invoice number as {first}",
                )
            )
    return issues


def build_vendor_master(data_dir: Path) -> VendorMaster:
    """Every seller in the dataset, standing in for a company's supplier list."""
    vendors = {}
    for label in sorted(data_dir.glob("*.json")):
        invoice = Invoice.model_validate_json(label.read_text())
        if invoice.seller.gstin and invoice.seller.name:
            vendors[invoice.seller.gstin] = invoice.seller.name
    return VendorMaster(vendors)
