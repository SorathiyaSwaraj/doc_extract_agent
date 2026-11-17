"""Apply a reviewer's corrections to an extracted invoice.

Corrections name fields the same way the review queue does (`seller.gstin`,
`totals.grand_total`, `line_items[2].quantity`), so a reviewer can copy a key
from `fields_to_check` straight into a correction. The corrected invoice goes
through the same model as any extraction, so a reviewer cannot store a value the
pipeline would reject, such as "abc" as an amount.
"""

import re
from typing import Any

from pydantic import ValidationError

from doc_extract.models import Invoice

_STEP = re.compile(r"([a-z_]+)(?:\[(\d+)\])?$")


class CorrectionError(ValueError):
    pass


def apply_corrections(
    invoice: dict | None, corrections: dict[str, Any]
) -> tuple[Invoice, dict[str, dict]]:
    """Return the corrected invoice and, per field, its value before and after."""
    data = Invoice.model_validate(invoice or {}).model_dump(mode="json")
    changes: dict[str, dict] = {}
    for path, value in corrections.items():
        parent, key = _locate(data, path)
        before = parent[key]
        parent[key] = value
        changes[path] = {"before": before, "after": value}
    try:
        corrected = Invoice.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in error.errors())
        raise CorrectionError(f"corrected invoice is invalid: {problems}") from error
    return corrected, changes


def _locate(data: dict, path: str) -> tuple[dict, str]:
    """The dict holding the field at path, and the field's key in it."""
    steps = path.split(".")
    node: Any = data
    for depth, step in enumerate(steps):
        match = _STEP.match(step)
        if not match or not isinstance(node, dict) or match[1] not in node:
            raise CorrectionError(f"unknown field {path!r}")
        name, index = match[1], match[2]
        last = depth == len(steps) - 1
        if index is None:
            if last:
                if isinstance(node[name], dict | list):
                    raise CorrectionError(f"{path!r} is a group; correct its fields one by one")
                return node, name
            node = node[name]
            continue
        items = node[name]
        if not isinstance(items, list) or int(index) >= len(items) or last:
            raise CorrectionError(f"unknown field {path!r}")
        node = items[int(index)]
    raise CorrectionError(f"unknown field {path!r}")
