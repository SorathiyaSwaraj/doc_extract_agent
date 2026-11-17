"""Indian GST rules shared by the synthetic generator, the validator, and the scorer."""

import re
from decimal import ROUND_HALF_UP, Decimal

STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PAISE = Decimal("0.01")


def gstin_check_char(first14: str) -> str:
    """Checksum character for the first 14 characters of a GSTIN.

    Each character's value is weighted 1 or 2 by position, and products are
    folded back into base 36, so a single wrong character changes the result.
    """
    total = 0
    for index, char in enumerate(first14):
        product = _GSTIN_CHARS.index(char) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    """Format and checksum only. Says nothing about whether the GSTIN is registered."""
    if len(gstin) != 15 or any(char not in _GSTIN_CHARS for char in gstin):
        return False
    pan = gstin[2:12]
    pan_shape_ok = pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha()
    return (
        gstin[:2] in STATES
        and pan_shape_ok
        and gstin[13] == "Z"
        and gstin[14] == gstin_check_char(gstin[:14])
    )


def round_paise(amount: Decimal) -> Decimal:
    """Round half up to paise, as invoices are printed. Python's default rounds half to even."""
    return amount.quantize(PAISE, rounding=ROUND_HALF_UP)


def canonical_state(value: str) -> str | None:
    """Accept 'Maharashtra', 'MAHARASHTRA', '27', or 'Maharashtra (27)'."""
    text = value.strip()
    if text.isdigit():
        return STATES.get(text.zfill(2))
    name = re.sub(r"\(\s*\d+\s*\)|\d", "", text).strip().casefold()
    by_name = {state.casefold(): state for state in STATES.values()}
    return by_name.get(name)
