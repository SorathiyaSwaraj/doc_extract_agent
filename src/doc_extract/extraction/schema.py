"""The JSON schema sent to the LLM, derived from the Pydantic models.

Deriving it keeps the prompt and the parser in step. Three changes make it easier
for a model to follow with constrained decoding:

- References are inlined, because providers differ in how well they handle `$ref`.
- Numbers become strings of plain digits, so no value passes through a float.
- Every key is required and may be null, so the model states "absent" explicitly
  instead of silently leaving keys out.
"""

from typing import Any

from pydantic import BaseModel

from doc_extract.models import Invoice

AMOUNT_HINT = "Digits and an optional decimal point only, no commas or currency, e.g. 1234.50."
DATE_HINT = "ISO date, YYYY-MM-DD."
_DROP = {"title", "default"}


def llm_schema(model: type[BaseModel] = Invoice) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    return _convert(schema, definitions)


def _convert(node: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return _convert(definitions[node["$ref"].split("/")[-1]], definitions)
    description = node.get("description")
    if "anyOf" in node:
        options = [option for option in node["anyOf"] if option.get("type") != "null"]
        nullable = len(options) < len(node["anyOf"])
        converted = _scalar(options, description) or _convert(options[0], definitions)
        if description and "description" not in converted:
            converted["description"] = description
        return {"anyOf": [converted, {"type": "null"}]} if nullable else converted

    converted = {key: value for key, value in node.items() if key not in _DROP}
    if node.get("type") == "object":
        converted["properties"] = {
            name: _convert(child, definitions) for name, child in node["properties"].items()
        }
        converted["required"] = list(node["properties"])
        converted["additionalProperties"] = False
    elif node.get("type") == "array":
        converted["items"] = _convert(node["items"], definitions)
    return converted


def _scalar(options: list[dict[str, Any]], description: str | None) -> dict[str, Any] | None:
    types = {option.get("type") for option in options}
    if types == {"number", "string"}:  # how Pydantic describes Decimal
        return {"type": "string", "description": _join(description, AMOUNT_HINT)}
    if options[0].get("format") == "date":
        return {"type": "string", "description": _join(description, DATE_HINT)}
    if types == {"string"}:
        return {"type": "string"}
    return None


def _join(description: str | None, hint: str) -> str:
    return f"{description} {hint}" if description else hint
