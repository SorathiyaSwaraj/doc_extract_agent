import json
from datetime import date

from doc_extract.agent.chat import GeminiChat, Turn
from doc_extract.agent.loop import NUDGE, run_agent
from doc_extract.agent.tools import Toolbox, declarations
from doc_extract.extraction.llm import LLMError
from doc_extract.models import Invoice
from doc_extract.validation.reference import VendorMaster

TODAY = date(2026, 11, 7)


def call(name, call_id=None, **args):
    function_call = {"name": name, "args": args}
    if call_id:
        function_call["id"] = call_id
    return {"role": "model", "parts": [{"functionCall": function_call, "thoughtSignature": "sig"}]}


class ScriptedChat:
    name, model = "scripted", "chat-model"

    def __init__(self, *messages):
        self.messages = list(messages)
        self.histories = []

    def turn(self, contents, tools, system):
        self.histories.append(json.loads(json.dumps(contents)))
        message = self.messages.pop(0)
        if isinstance(message, Exception):
            raise message
        return Turn(message, 100, 20, 0.5, self.model)


def dump(invoice):
    return json.loads(invoice.model_dump_json())


def test_read_validate_submit(sample_invoice):
    invoice, pdf = sample_invoice
    chat = ScriptedChat(
        call("read_text", "c1"),
        call("validate", "c2", invoice=dump(invoice)),
        call("submit", "c3", invoice=dump(invoice)),
    )

    prediction, trace = run_agent(pdf, "d", chat, today=TODAY)

    assert trace == ["read_text", "validate", "submit"]
    assert Invoice.model_validate(prediction.invoice) == invoice
    assert (prediction.input_tokens, prediction.output_tokens) == (300, 60)
    second, third = chat.histories[1], chat.histories[2]
    text_result = second[-1]["parts"][0]["functionResponse"]
    assert text_result["id"] == "c1"
    assert invoice.seller.gstin in text_result["response"]["text"]
    assert third[-1]["parts"][0]["functionResponse"]["response"] == {"problems": []}
    assert second[1]["parts"][0]["thoughtSignature"] == "sig"  # model turns kept verbatim


def test_view_page_attaches_a_zoomed_image(sample_invoice):
    invoice, pdf = sample_invoice
    chat = ScriptedChat(call("view_page", part="bottom"), call("submit", invoice=dump(invoice)))

    run_agent(pdf, "d", chat, today=TODAY)

    parts = chat.histories[1][-1]["parts"]
    assert parts[0]["functionResponse"]["name"] == "view_page"
    assert parts[1]["inline_data"]["mime_type"] == "image/png"


def test_budget_exhausted_uses_last_validated_draft(sample_invoice):
    invoice, pdf = sample_invoice
    chat = ScriptedChat(call("validate", invoice=dump(invoice)), *[call("read_text")] * 3)

    prediction, trace = run_agent(pdf, "d", chat, max_turns=4, today=TODAY)

    assert trace[-1] == "budget_exhausted"
    assert Invoice.model_validate(prediction.invoice) == invoice
    assert any(part.get("text") == NUDGE for part in chat.histories[-1][-1]["parts"])


def test_text_reply_gets_a_nudge(sample_invoice):
    invoice, pdf = sample_invoice
    chat = ScriptedChat(
        {"role": "model", "parts": [{"text": "Here is the invoice..."}]},
        call("submit", invoice=dump(invoice)),
    )

    _, trace = run_agent(pdf, "d", chat, today=TODAY)

    assert trace == ["no_tool_call", "submit"]
    assert chat.histories[1][-1] == {"role": "user", "parts": [{"text": NUDGE}]}


def test_invalid_submission_is_returned_to_the_model(sample_invoice):
    invoice, pdf = sample_invoice
    bad = {**dump(invoice), "totals": {"grand_total": "lots"}}
    chat = ScriptedChat(call("submit", invoice=bad), call("submit", invoice=dump(invoice)))

    prediction, trace = run_agent(pdf, "d", chat, today=TODAY)

    response = chat.histories[1][-1]["parts"][0]["functionResponse"]["response"]
    assert "grand_total" in response["error"]
    assert trace == ["submit", "submit"]
    assert prediction.invoice is not None


def test_chat_error_is_recorded(sample_invoice):
    _, pdf = sample_invoice

    prediction, trace = run_agent(pdf, "d", ScriptedChat(LLMError("quota")), today=TODAY)

    assert prediction.error == "quota" and prediction.invoice is None
    assert trace == ["error"]


def test_tools_validate_and_lookup(sample_invoice):
    invoice, pdf = sample_invoice
    toolbox = Toolbox(pdf, vendors=VendorMaster({}), today=TODAY)

    problems = toolbox.run("validate", {"invoice": dump(invoice)})[0]["problems"]
    lookup = toolbox.run("lookup_vendor", {"name": "anything"})[0]

    assert [p["message"] for p in problems if "vendor master" in p["message"]]
    assert lookup == {"matches": []}
    assert "error" in toolbox.run("view_page", {"part": "left"})[0]
    assert "error" in toolbox.run("delete_everything", {})[0]


def test_declarations_are_read_only_tools():
    names = {tool["name"] for tool in declarations()}

    assert names == {"read_text", "view_page", "validate", "lookup_vendor", "submit"}


def test_gemini_chat_payload():
    sent = []

    def post(url, payload, headers, timeout):
        sent.append(payload)
        return {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
            "modelVersion": "v",
        }

    turn = GeminiChat("k", post=post).turn([{"role": "user", "parts": []}], [{"name": "t"}], "sys")

    assert sent[0]["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert sent[0]["tools"] == [{"functionDeclarations": [{"name": "t"}]}]
    assert (turn.input_tokens, turn.output_tokens, turn.calls) == (10, 2, [])


def test_run_batch_resumes(tmp_path):
    from doc_extract.agent.run import run_batch
    from doc_extract.synthetic.dataset import build_dataset

    build_dataset(tmp_path / "data", count=10, seed="batch")
    seen = []

    def extract_one(pdf, doc_id):
        seen.append(doc_id)
        return {"doc_id": doc_id, "trace": ["submit"]}

    out = tmp_path / "rows.jsonl"
    first = run_batch(tmp_path / "data", "train", extract_one, out, limit=2)
    second = run_batch(tmp_path / "data", "train", extract_one, out, limit=4)

    assert (first, second) == (2, 2)
    assert len(set(seen)) == 4
