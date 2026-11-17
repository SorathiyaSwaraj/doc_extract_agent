"""Random but internally consistent GST invoices, used as gold labels.

The label is generated first and the PDF is rendered from it, so the label is
exactly right by construction. Labels hold canonical values (a date, a Decimal,
a state name); how they are printed is the renderer's choice.
"""

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from doc_extract.gst import STATES, gstin_check_char, round_paise
from doc_extract.models import Invoice, LineItem, Party, Totals

# GST 2.0 replaced the 12% and 28% slabs on 22 September 2025. Rates are drawn
# from the slabs in force on the invoice date, not from each product's real rate.
SLAB_CHANGE = date(2025, 9, 22)
RATES_BEFORE = (Decimal(5), Decimal(12), Decimal(18), Decimal(28))
RATES_AFTER = (Decimal(5), Decimal(18), Decimal(40))
RATE_WEIGHTS_AFTER = (4, 10, 1)

CITIES = {
    "27": ("Mumbai", "Pune", "Nagpur"),
    "29": ("Bengaluru", "Mysuru"),
    "24": ("Ahmedabad", "Surat", "Vadodara"),
    "33": ("Chennai", "Coimbatore"),
    "07": ("New Delhi",),
    "36": ("Hyderabad",),
    "19": ("Kolkata",),
    "09": ("Noida", "Lucknow"),
    "08": ("Jaipur",),
    "32": ("Kochi",),
}

NAME_FIRST = (
    "Shree",
    "Sai",
    "Om",
    "Bharat",
    "Apex",
    "Vertex",
    "Ganesh",
    "Lakshmi",
    "Nova",
    "Indus",
)
NAME_SECOND = (
    "Steel",
    "Traders",
    "Polymers",
    "Electricals",
    "Infotech",
    "Textiles",
    "Agro",
    "Pharma",
)
NAME_SUFFIX = ("Pvt Ltd", "LLP", "Enterprises", "& Co", "Industries", "Private Limited")
STREETS = ("MG Road", "Station Road", "Ring Road", "Link Road", "Industrial Estate", "Main Road")
AREAS = ("Andheri East", "Whitefield", "Naroda GIDC", "Guindy", "Okhla Phase II", "Hitech City")

ITEMS = (
    ("Hex bolts M10 x 50", "7318", (2, 40)),
    ("Copper wire 2.5 sq mm (90 m coil)", "8544", (900, 4200)),
    ("A4 copier paper, 500 sheets", "4802", (180, 420)),
    ("LED panel light 18W", "9405", (350, 1600)),
    ("PVC pipe 4 inch, 6 m", "3917", (450, 1300)),
    ("Industrial gloves (pair)", "4015", (35, 160)),
    ("Laptop stand, aluminium", "8473", (700, 2600)),
    ("Software development services", "998314", (15000, 250000)),
    ("Annual maintenance contract", "998713", (5000, 90000)),
    ("Freight charges", "996511", (500, 12000)),
)


def generate_invoice(rng: random.Random) -> Invoice:
    seller_state = rng.choice(tuple(CITIES))
    # Half are intra-state (CGST + SGST), half inter-state (IGST), as in real B2B flows.
    buyer_state = seller_state if rng.random() < 0.5 else rng.choice(tuple(CITIES))
    is_b2b = rng.random() < 0.8
    invoice_date = date(2024, 4, 1) + timedelta(days=rng.randrange(900))

    seller = _party(rng, seller_state, with_gstin=True)
    buyer = _party(rng, buyer_state, with_gstin=is_b2b)
    items = tuple(_line_item(rng, invoice_date) for _ in range(rng.randint(1, 8)))
    totals = _totals(items, intra_state=seller_state == buyer_state)

    return Invoice(
        invoice_number=_invoice_number(rng, invoice_date),
        invoice_date=invoice_date,
        seller=seller,
        buyer=buyer,
        place_of_supply=STATES[buyer_state],
        line_items=items,
        totals=totals,
    )


def generate_gstin(rng: random.Random, state_code: str) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    # The fourth PAN letter is the holder type: C company, F firm, P person.
    pan = (
        "".join(rng.choice(letters) for _ in range(3))
        + rng.choice("CFP")
        + rng.choice(letters)
        + f"{rng.randrange(10000):04d}"
        + rng.choice(letters)
    )
    first14 = f"{state_code}{pan}{rng.choice('123')}Z"
    return first14 + gstin_check_char(first14)


def _party(rng: random.Random, state_code: str, with_gstin: bool) -> Party:
    name = f"{rng.choice(NAME_FIRST)} {rng.choice(NAME_SECOND)} {rng.choice(NAME_SUFFIX)}"
    address = (
        f"{rng.randint(1, 450)}, {rng.choice(STREETS)}, {rng.choice(AREAS)}, "
        f"{rng.choice(CITIES[state_code])} {rng.randint(110001, 799999)}"
    )
    return Party(
        name=name,
        gstin=generate_gstin(rng, state_code) if with_gstin else None,
        address=address,
        state=STATES[state_code],
    )


def _line_item(rng: random.Random, invoice_date: date) -> LineItem:
    description, hsn_sac, (low, high) = rng.choice(ITEMS)
    is_service = len(hsn_sac) == 6
    quantity = Decimal(1) if is_service else Decimal(rng.randint(1, 60))
    unit_price = round_paise(Decimal(rng.uniform(low, high)))
    if invoice_date < SLAB_CHANGE:
        rate = rng.choice(RATES_BEFORE)
    else:
        rate = rng.choices(RATES_AFTER, weights=RATE_WEIGHTS_AFTER)[0]
    return LineItem(
        description=description,
        hsn_sac=hsn_sac,
        quantity=quantity,
        unit_price=unit_price,
        taxable_value=round_paise(quantity * unit_price),
        gst_rate=rate,
    )


def _totals(items: tuple[LineItem, ...], intra_state: bool) -> Totals:
    """Tax is rounded per line and then summed, the way most billing software does it."""
    taxable = sum((item.taxable_value for item in items), Decimal(0))
    if intra_state:
        half = sum((round_paise(i.taxable_value * i.gst_rate / 200) for i in items), Decimal(0))
        cgst, sgst, igst = half, half, None
        tax = half * 2
    else:
        igst = sum((round_paise(i.taxable_value * i.gst_rate / 100) for i in items), Decimal(0))
        cgst, sgst = None, None
        tax = igst
    raw_total = taxable + tax
    grand_total = raw_total.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Totals(
        taxable_value=taxable,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        round_off=grand_total - raw_total,
        grand_total=grand_total.quantize(Decimal("0.01")),
    )


def _invoice_number(rng: random.Random, invoice_date: date) -> str:
    # Indian financial years run April to March, and many invoice series reset each year.
    start = invoice_date.year if invoice_date.month >= 4 else invoice_date.year - 1
    serial = rng.randint(1, 4999)
    formats = (
        f"INV/{start % 100}-{(start + 1) % 100}/{serial:04d}",
        f"GST-{invoice_date.year}-{serial}",
        f"TI/{serial:04d}/{start}-{(start + 1) % 100:02d}",
        f"A{serial:05d}",
    )
    return rng.choice(formats)
