"""Rule-based checks on an extracted invoice. No LLM, no document, no gold label.

Checks look for values that cannot all be right together: a total that is not
the sum of its parts, a GSTIN whose checksum fails, CGST on an inter-state sale.
They flag that something is wrong, not what; a misread that is still internally
consistent (TI read as Tl in an invoice number) passes every check.

Errors break a GST rule or arithmetic. Warnings are unusual but legal, such as a
rate outside the standard slabs, which special goods really have.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from doc_extract.gst import STATES, canonical_state, is_valid_gstin, round_paise
from doc_extract.models import Invoice

GST_START = date(2017, 7, 1)
SLAB_CHANGE = date(2025, 9, 22)
# Standard slabs, plus 0, 0.25 (rough diamonds), and 3 (gold) that apply in both periods
SLABS_BEFORE = {Decimal(r) for r in ("0", "0.25", "3", "5", "12", "18", "28")}
SLABS_AFTER = {Decimal(r) for r in ("0", "0.25", "3", "5", "18", "40")}
PAISA = Decimal("0.01")
MAX_ROUND_OFF = Decimal("0.50")
REQUIRED = {
    "invoice_number": lambda i: i.invoice_number,
    "invoice_date": lambda i: i.invoice_date,
    "seller.gstin": lambda i: i.seller.gstin,
    "totals.taxable_value": lambda i: i.totals.taxable_value,
    "totals.grand_total": lambda i: i.totals.grand_total,
}


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Literal["error", "warning"]
    fields: tuple[str, ...]
    message: str


def validate(invoice: Invoice, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    for check in CHECKS:
        issues.extend(check(invoice, today))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _missing(invoice: Invoice, today: date) -> list[Issue]:
    return [
        Issue("missing", "error", (name,), f"{name} is empty")
        for name, get in REQUIRED.items()
        if get(invoice) is None
    ]


def _gstins(invoice: Invoice, today: date) -> list[Issue]:
    issues = []
    for role, party in (("seller", invoice.seller), ("buyer", invoice.buyer)):
        if not party.gstin:
            continue
        field = f"{role}.gstin"
        if not is_valid_gstin(party.gstin):
            issues.append(
                Issue("gstin_invalid", "error", (field,), f"{field} fails format or checksum")
            )
            continue
        state = canonical_state(party.state) if party.state else None
        if state and STATES.get(party.gstin[:2]) != state:
            issues.append(
                Issue(
                    "gstin_state_mismatch",
                    "error",
                    (field, f"{role}.state"),
                    f"{field} starts with {party.gstin[:2]}, which is not {state}",
                )
            )
    return issues


def _line_arithmetic(invoice: Invoice, today: date) -> list[Issue]:
    issues = []
    for index, item in enumerate(invoice.line_items):
        if None in (item.quantity, item.unit_price, item.taxable_value):
            continue
        expected = round_paise(item.quantity * item.unit_price)
        if abs(expected - item.taxable_value) > PAISA:
            issues.append(
                Issue(
                    "line_amount",
                    "error",
                    (f"line_items[{index}].taxable_value",),
                    f"line {index + 1}: {item.quantity} x {item.unit_price} = {expected}, "
                    f"not {item.taxable_value}",
                )
            )
    return issues


def _taxable_sum(invoice: Invoice, today: date) -> list[Issue]:
    values = [item.taxable_value for item in invoice.line_items]
    total = invoice.totals.taxable_value
    if not values or None in values or total is None:
        return []
    if abs(sum(values) - total) > PAISA:
        return [
            Issue(
                "taxable_sum",
                "error",
                ("totals.taxable_value", "line_items"),
                f"line amounts sum to {sum(values)}, but taxable value is {total}",
            )
        ]
    return []


def _tax_type(invoice: Invoice, today: date) -> list[Issue]:
    """Same state: CGST and SGST in equal halves. Different states: IGST only."""
    seller = canonical_state(invoice.seller.state) if invoice.seller.state else None
    place = canonical_state(invoice.place_of_supply) if invoice.place_of_supply else None
    if not seller or not place:
        return []
    totals = invoice.totals
    if seller == place:
        if totals.igst is not None or totals.cgst is None or totals.sgst is None:
            return [_tax_issue("intra-state supply needs CGST and SGST, not IGST")]
        if totals.cgst != totals.sgst:
            return [_tax_issue(f"CGST {totals.cgst} and SGST {totals.sgst} should be equal")]
    elif totals.igst is None or totals.cgst is not None or totals.sgst is not None:
        return [_tax_issue("inter-state supply needs IGST, not CGST and SGST")]
    return []


def _tax_issue(message: str) -> Issue:
    return Issue(
        "tax_type",
        "error",
        ("totals.cgst", "totals.sgst", "totals.igst", "place_of_supply"),
        message,
    )


def _tax_amount(invoice: Invoice, today: date) -> list[Issue]:
    """Recompute tax from line rates. Billing software rounds per line or per rate,
    so allow one paisa per line."""
    items = invoice.line_items
    if not items or any(i.taxable_value is None or i.gst_rate is None for i in items):
        return []
    expected = sum((round_paise(i.taxable_value * i.gst_rate / 100) for i in items), Decimal(0))
    totals = invoice.totals
    printed = [v for v in (totals.cgst, totals.sgst, totals.igst) if v is not None]
    if not printed:
        return []
    tolerance = PAISA * (len(items) + 1)
    if abs(sum(printed) - expected) > tolerance:
        return [
            Issue(
                "tax_amount",
                "error",
                ("totals.cgst", "totals.sgst", "totals.igst"),
                f"tax at the line rates is {expected}, but the invoice shows {sum(printed)}",
            )
        ]
    return []


def _grand_total(invoice: Invoice, today: date) -> list[Issue]:
    totals = invoice.totals
    if totals.taxable_value is None or totals.grand_total is None:
        return []
    parts = [totals.taxable_value, totals.cgst, totals.sgst, totals.igst, totals.cess]
    expected = sum((p for p in parts if p is not None), Decimal(0)) + (totals.round_off or 0)
    issues = []
    if abs(expected - totals.grand_total) > PAISA:
        issues.append(
            Issue(
                "grand_total",
                "error",
                ("totals.grand_total",),
                f"taxable value + taxes + round off = {expected}, "
                f"but grand total is {totals.grand_total}",
            )
        )
    if totals.round_off is not None and abs(totals.round_off) > MAX_ROUND_OFF:
        issues.append(
            Issue(
                "round_off",
                "error",
                ("totals.round_off",),
                f"round off {totals.round_off} exceeds 0.50",
            )
        )
    return issues


def _rates(invoice: Invoice, today: date) -> list[Issue]:
    if invoice.invoice_date is None:
        return []
    slabs = SLABS_BEFORE if invoice.invoice_date < SLAB_CHANGE else SLABS_AFTER
    return [
        Issue(
            "rate_unusual",
            "warning",
            (f"line_items[{index}].gst_rate",),
            f"line {index + 1}: {item.gst_rate}% is not a standard slab on {invoice.invoice_date}",
        )
        for index, item in enumerate(invoice.line_items)
        if item.gst_rate is not None and item.gst_rate not in slabs
    ]


def _date(invoice: Invoice, today: date) -> list[Issue]:
    when = invoice.invoice_date
    if when is None:
        return []
    if when > today:
        return [Issue("date_future", "error", ("invoice_date",), f"{when} is in the future")]
    if when < GST_START:
        return [Issue("date_before_gst", "error", ("invoice_date",), f"{when} is before GST began")]
    return []


CHECKS: list[Callable[[Invoice, date], list[Issue]]] = [
    _missing,
    _gstins,
    _line_arithmetic,
    _taxable_sum,
    _tax_type,
    _tax_amount,
    _grand_total,
    _rates,
    _date,
]
