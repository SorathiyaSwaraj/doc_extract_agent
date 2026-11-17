"""Render a gold invoice to PDF in one of several layouts.

Layouts differ in structure, and each document also draws its own labels, date
format, and number grouping, so a model cannot succeed by memorising one template.
"""

import io
import random
from dataclasses import dataclass
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from doc_extract.gst import STATES
from doc_extract.models import Invoice, Party

LAYOUTS = ("classic", "compact", "modern")
STATE_CODES = {name: code for code, name in STATES.items()}

LABELS = {
    "number": ("Invoice No.", "Invoice #", "Bill No.", "Tax Invoice No."),
    "date": ("Invoice Date", "Date", "Dated", "Bill Date"),
    "buyer": ("Bill To", "Buyer", "Billed To", "Customer"),
    "taxable": ("Taxable Value", "Sub Total", "Taxable Amount"),
    "total": ("Grand Total", "Total Amount", "Amount Payable", "Invoice Total"),
}
DATE_FORMATS = ("%d/%m/%Y", "%d-%b-%Y", "%Y-%m-%d", "%d.%m.%Y")
GROUPINGS = ("indian", "western", "none")
CURRENCY_PREFIXES = ("Rs. ", "INR ", "")

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8.5, leading=10.5)
BOLD = ParagraphStyle("bold", parent=BODY, fontName="Helvetica-Bold")
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=15, leading=18)


@dataclass(frozen=True)
class Style:
    layout: str
    labels: dict[str, str]
    date_format: str
    grouping: str
    currency_prefix: str


def choose_style(rng: random.Random, layout: str) -> Style:
    return Style(
        layout=layout,
        labels={key: rng.choice(options) for key, options in LABELS.items()},
        date_format=rng.choice(DATE_FORMATS),
        grouping=rng.choice(GROUPINGS),
        currency_prefix=rng.choice(CURRENCY_PREFIXES),
    )


def format_amount(value: Decimal, grouping: str) -> str:
    """Indian grouping puts commas after the thousands, then every two digits: 12,34,567.89."""
    sign = "-" if value < 0 else ""
    whole, fraction = f"{abs(value):.2f}".split(".")
    if grouping == "western":
        whole = f"{int(whole):,}"
    elif grouping == "indian" and len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while head:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(pairs) + "," + tail
    return f"{sign}{whole}.{fraction}"


def render_invoice(invoice: Invoice, style: Style) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="Invoice",
        invariant=True,
    )
    builders = {"classic": _classic, "compact": _compact, "modern": _modern}
    document.build(builders[style.layout](invoice, style))
    return buffer.getvalue()


def _money(value: Decimal | None, style: Style, prefix: bool = False) -> str:
    text = format_amount(value, style.grouping)
    return style.currency_prefix + text if prefix else text


def _number(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _state(name: str) -> str:
    return f"{name} ({STATE_CODES[name]})"


def _party_lines(party: Party) -> list[str]:
    lines = [f"<b>{party.name}</b>", party.address, f"State: {_state(party.state)}"]
    lines.append(f"GSTIN: {party.gstin}" if party.gstin else "GSTIN: Unregistered")
    return lines


def _meta_rows(invoice: Invoice, style: Style) -> list[tuple[str, str]]:
    return [
        (style.labels["number"], invoice.invoice_number),
        (style.labels["date"], invoice.invoice_date.strftime(style.date_format)),
        ("Place of Supply", _state(invoice.place_of_supply)),
    ]


def _item_rows(invoice: Invoice, style: Style, with_serial: bool) -> list[list]:
    header = ["Description", "HSN/SAC", "Qty", "Rate", "GST %", "Amount"]
    rows = [(["Sr"] if with_serial else []) + header]
    for number, item in enumerate(invoice.line_items, start=1):
        row = [
            Paragraph(item.description, BODY),
            item.hsn_sac,
            _number(item.quantity),
            _money(item.unit_price, style),
            f"{_number(item.gst_rate)}%",
            _money(item.taxable_value, style),
        ]
        rows.append(([str(number)] if with_serial else []) + row)
    return rows


def _total_rows(invoice: Invoice, style: Style) -> list[tuple[str, str]]:
    totals = invoice.totals
    rows = [(style.labels["taxable"], _money(totals.taxable_value, style))]
    for label, value in (("CGST", totals.cgst), ("SGST", totals.sgst), ("IGST", totals.igst)):
        if value is not None:
            rows.append((label, _money(value, style)))
    rows.append(("Round Off", _money(totals.round_off, style)))
    rows.append((style.labels["total"], _money(totals.grand_total, style, prefix=True)))
    return rows


def _totals_table(invoice: Invoice, style: Style, boxed: bool) -> Table:
    table = Table(_total_rows(invoice, style), colWidths=[45 * mm, 35 * mm], hAlign="RIGHT")
    commands = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
    ]
    if boxed:
        commands.append(("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#1f4e79")))
    table.setStyle(TableStyle(commands))
    return table


def _classic(invoice: Invoice, style: Style) -> list:
    title = Paragraph("TAX INVOICE", ParagraphStyle("t", parent=TITLE, alignment=TA_CENTER))
    seller = [Paragraph(line, BODY) for line in _party_lines(invoice.seller)]
    meta = [
        Paragraph(f"<b>{label}:</b> {value}", BODY) for label, value in _meta_rows(invoice, style)
    ]
    header = Table([[seller, meta]], colWidths=[100 * mm, 80 * mm])
    header.setStyle(
        TableStyle(
            [("GRID", (0, 0), (-1, -1), 0.6, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        )
    )
    buyer_lines = [f"<b>{style.labels['buyer']}</b>", *_party_lines(invoice.buyer)]
    buyer = Table([[[Paragraph(line, BODY) for line in buyer_lines]]], colWidths=[180 * mm])
    buyer.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.black)]))
    items = Table(
        _item_rows(invoice, style, with_serial=True),
        colWidths=[9 * mm, 66 * mm, 20 * mm, 15 * mm, 25 * mm, 15 * mm, 30 * mm],
        repeatRows=1,
    )
    items.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [
        title,
        Spacer(1, 4 * mm),
        header,
        Spacer(1, 3 * mm),
        buyer,
        Spacer(1, 4 * mm),
        items,
        Spacer(1, 4 * mm),
        _totals_table(invoice, style, boxed=False),
    ]


def _compact(invoice: Invoice, style: Style) -> list:
    flow = [
        Paragraph(line, TITLE if i == 0 else BODY)
        for i, line in enumerate(_party_lines(invoice.seller))
    ]
    flow.append(Spacer(1, 3 * mm))
    flow += [Paragraph(f"{label}: {value}", BODY) for label, value in _meta_rows(invoice, style)]
    flow.append(Spacer(1, 3 * mm))
    flow.append(Paragraph(f"{style.labels['buyer']}:", BOLD))
    flow += [Paragraph(line, BODY) for line in _party_lines(invoice.buyer)]
    flow.append(Spacer(1, 4 * mm))
    items = Table(
        _item_rows(invoice, style, with_serial=False),
        colWidths=[72 * mm, 20 * mm, 14 * mm, 25 * mm, 15 * mm, 34 * mm],
    )
    items.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.black),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [*flow, items, Spacer(1, 3 * mm), _totals_table(invoice, style, boxed=False)]


def _modern(invoice: Invoice, style: Style) -> list:
    band_style = ParagraphStyle("band", parent=TITLE, textColor=colors.white)
    band = Table(
        [
            [
                Paragraph(invoice.seller.name, band_style),
                Paragraph("INVOICE", ParagraphStyle("r", parent=band_style, alignment=TA_RIGHT)),
            ]
        ],
        colWidths=[120 * mm, 60 * mm],
    )
    band.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1f4e79")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    seller = [Paragraph(line, BODY) for line in _party_lines(invoice.seller)[1:]]
    buyer = [
        Paragraph(f"<b>{style.labels['buyer']}</b>", BODY),
        *[Paragraph(line, BODY) for line in _party_lines(invoice.buyer)],
    ]
    meta = Table(_meta_rows(invoice, style), colWidths=[28 * mm, 32 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8),
                ("FONT", (1, 0), (1, -1), "Helvetica", 8),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#1f4e79")),
            ]
        )
    )
    # The details column is wider than its 60 mm box to leave room for cell padding.
    parties = Table([[seller, buyer, meta]], colWidths=[57 * mm, 57 * mm, 66 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    rows = _item_rows(invoice, style, with_serial=False)
    # Taxable amount before rate, unlike the other layouts
    rows = [[r[0], r[1], r[2], r[3], r[5], r[4]] for r in rows]
    rows[0][4] = "Taxable"
    items = Table(rows, colWidths=[70 * mm, 20 * mm, 14 * mm, 26 * mm, 34 * mm, 16 * mm])
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f8")]),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [
        band,
        Spacer(1, 4 * mm),
        parties,
        Spacer(1, 5 * mm),
        items,
        Spacer(1, 4 * mm),
        _totals_table(invoice, style, boxed=True),
    ]
