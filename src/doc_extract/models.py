"""What an invoice says, as read from the document.

These models record extraction, not truth. Every field is optional and nothing is
checked against tax rules here, because a wrong or missing value must still be
stored so validation can flag it and a reviewer can fix it. Field descriptions
become part of the JSON schema sent to the LLM, so they are written as instructions.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_PRINTED_NUMBER_NOISE = re.compile(r"^(?:Rs\.?|INR|₹)|[\s,%₹]", re.IGNORECASE)


def _parse_printed_number(value: object) -> object:
    """Accept numbers as printed, such as 'Rs. 2,81,438.00' or '18%'.

    Floats go through their shortest repr, so a JSON 1034.15 becomes Decimal('1034.15')
    rather than its binary expansion, Decimal('1034.150000000000090949...').
    """
    if isinstance(value, float):
        value = repr(value)
    if isinstance(value, str):
        value = _PRINTED_NUMBER_NOISE.sub("", value.strip())
        # OCR often reads a comma as a point, giving 1.072.40 for 1,072.40. With more
        # than one point, every point but the last must be a thousands separator.
        if value.count(".") > 1:
            head, _, tail = value.rpartition(".")
            value = head.replace(".", "") + "." + tail
    return value


Number = Annotated[Decimal, BeforeValidator(_parse_printed_number)]
Money = Number


class ExtractionModel(BaseModel):
    """Base model: results are immutable, and corrections make a new copy."""

    model_config = ConfigDict(frozen=True)


class Party(ExtractionModel):
    name: str | None = Field(None, description="Legal or trade name exactly as printed.")
    gstin: str | None = Field(
        None, description="15-character GSTIN as printed, without spaces. Null if absent."
    )
    address: str | None = Field(None, description="Full address as printed, one line.")
    state: str | None = Field(None, description="State name only, without its code.")


class LineItem(ExtractionModel):
    description: str | None = None
    hsn_sac: str | None = Field(None, description="HSN or SAC code as printed.")
    quantity: Number | None = None
    unit_price: Money | None = Field(None, description="Price per unit before tax.")
    taxable_value: Money | None = Field(None, description="Line amount before tax.")
    gst_rate: Number | None = Field(
        None, description="Total GST rate in percent, for example 18 for 18%."
    )


class Totals(ExtractionModel):
    taxable_value: Money | None = Field(None, description="Sum of line amounts before tax.")
    cgst: Money | None = None
    sgst: Money | None = None
    igst: Money | None = None
    cess: Money | None = None
    round_off: Money | None = Field(None, description="Rounding adjustment, with its sign.")
    grand_total: Money | None = Field(None, description="Final amount payable.")


class Invoice(ExtractionModel):
    invoice_number: str | None = None
    invoice_date: date | None = None
    seller: Party = Party()
    buyer: Party = Party()
    place_of_supply: str | None = Field(None, description="State name only, without its code.")
    line_items: tuple[LineItem, ...] = ()
    totals: Totals = Totals()
    currency: str = Field("INR", description="ISO 4217 code. INR unless another is printed.")
