"""A regular-expression baseline over the rebuilt page text. No LLM.

It uses generic invoice conventions (labels such as "Invoice No." or "Grand
Total", GSTIN shape, the first GSTIN belonging to the seller) rather than this
generator's exact templates, and it does not attempt line items, names in odd
places, or addresses. It shows what an LLM adds, and gives the scoring harness
real predictions without an API key.
"""

import re
from datetime import date, datetime
from decimal import Decimal

from doc_extract.models import Invoice, Party, Totals

RULES_VERSION = "rules-v1"

GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b")
AMOUNT = re.compile(r"-?\d[\d,]*\.\d{2}\b")
# [ \t] rather than \s: a label must not run across a line break, or "TAX INVOICE"
# followed by a seller named "Nova..." reads as "Invoice No" with number "va".
INVOICE_NUMBER = re.compile(
    r"\b(?:invoice|bill)[ \t]*(?:no\b\.?|#|number\b)[ \t]*:?[ \t]*([A-Z0-9][\w/-]*)",
    re.IGNORECASE,
)
DATE = re.compile(r"\b(?:invoice date|bill date|dated|date)[ \t]*:?[ \t]*([\w./-]+)", re.IGNORECASE)
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y")
STATE = re.compile(r"\bState[ \t]*:[ \t]*([A-Za-z][A-Za-z &]+?)[ \t]*\([ \t]*\d{2}[ \t]*\)")
PLACE_OF_SUPPLY = re.compile(
    r"Place of Supply[ \t]*:?[ \t]*([A-Za-z][A-Za-z &]+?)[ \t]*(?:\(|$)", re.M
)
TOTAL_LABELS = {
    "taxable_value": re.compile(r"^(?:sub ?total|taxable (?:value|amount))\b", re.IGNORECASE),
    "cgst": re.compile(r"^cgst\b", re.IGNORECASE),
    "sgst": re.compile(r"^sgst\b", re.IGNORECASE),
    "igst": re.compile(r"^igst\b", re.IGNORECASE),
    "round_off": re.compile(r"^round[\s-]?off\b", re.IGNORECASE),
    "grand_total": re.compile(
        r"^(?:grand total|total amount|amount payable|invoice total)\b", re.IGNORECASE
    ),
}
TITLE = re.compile(r"^(?:tax\s+)?invoice$", re.IGNORECASE)


def extract_with_rules(text: str) -> Invoice:
    gstins = GSTIN.findall(text)
    states = STATE.findall(text)
    seller_gstin = gstins[0] if gstins else None
    # An unregistered buyer prints no GSTIN, so a second one is the buyer's only if present.
    buyer_gstin = gstins[1] if len(gstins) > 1 else None
    number = INVOICE_NUMBER.search(text)
    place = PLACE_OF_SUPPLY.search(text)
    return Invoice(
        invoice_number=number.group(1) if number else None,
        invoice_date=_first_date(text),
        seller=Party(
            name=_seller_name(text),
            gstin=seller_gstin,
            state=states[0] if states else None,
        ),
        buyer=Party(gstin=buyer_gstin, state=states[1] if len(states) > 1 else None),
        place_of_supply=place.group(1) if place else None,
        totals=_totals(text),
    )


def _first_date(text: str) -> date | None:
    for match in DATE.finditer(text):
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(match.group(1), date_format).date()
            except ValueError:
                continue
    return None


def _seller_name(text: str) -> str | None:
    """The first text on the page that is not a title; rows hold columns split by wide gaps."""
    for row in text.splitlines():
        for cell in re.split(r"\s{3,}", row.strip()):
            if cell and not TITLE.match(cell):
                return cell
    return None


def _totals(text: str) -> Totals:
    values: dict[str, Decimal] = {}
    for row in text.splitlines():
        # A totals label can share a row with other columns, so look at each cell.
        cells = re.split(r"\s{3,}", row.strip())
        for index, cell in enumerate(cells):
            for name, label in TOTAL_LABELS.items():
                if name in values or not label.match(cell):
                    continue
                amounts = AMOUNT.findall(" ".join(cells[index:]))
                if amounts:
                    values[name] = Decimal(amounts[-1].replace(",", ""))
    return Totals(**values)
