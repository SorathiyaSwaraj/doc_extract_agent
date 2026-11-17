import json

from doc_extract.extraction.schema import AMOUNT_HINT, llm_schema
from doc_extract.models import Invoice


def walk(node):
    yield node
    for value in node.values():
        if isinstance(value, dict):
            yield from walk(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from walk(item)


def test_schema_has_no_references_or_numbers():
    schema = llm_schema()

    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    assert all(node.get("type") != "number" for node in walk(schema))


def test_every_object_requires_all_its_keys():
    for node in walk(llm_schema()):
        if node.get("type") == "object":
            assert node["required"] == list(node["properties"])
            assert node["additionalProperties"] is False


def test_amounts_are_nullable_strings_with_instructions():
    totals = llm_schema()["properties"]["totals"]["properties"]

    grand_total = totals["grand_total"]
    assert grand_total["anyOf"][1] == {"type": "null"}
    assert grand_total["anyOf"][0]["type"] == "string"
    assert grand_total["anyOf"][0]["description"].startswith("Final amount payable.")
    assert AMOUNT_HINT in grand_total["anyOf"][0]["description"]


def test_field_descriptions_survive():
    seller = llm_schema()["properties"]["seller"]["properties"]

    assert "GSTIN" in seller["gstin"]["anyOf"][0]["description"]


def test_a_response_shaped_by_the_schema_parses_into_invoice():
    response = {
        "invoice_number": "A1",
        "invoice_date": "2025-07-15",
        "seller": {"name": "S", "gstin": None, "address": None, "state": "Gujarat"},
        "buyer": {"name": None, "gstin": None, "address": None, "state": None},
        "place_of_supply": None,
        "line_items": [
            {
                "description": "Bolts",
                "hsn_sac": "7318",
                "quantity": "2",
                "unit_price": "10.50",
                "taxable_value": "21.00",
                "gst_rate": "18",
            }
        ],
        "totals": {
            "taxable_value": "21.00",
            "cgst": None,
            "sgst": None,
            "igst": "3.78",
            "cess": None,
            "round_off": "0.22",
            "grand_total": "25.00",
        },
        "currency": "INR",
    }
    assert set(response) == set(llm_schema()["properties"])

    assert Invoice.model_validate(response).totals.igst is not None
