"""The MCP server, driven by a real MCP client, against the real API in process."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from doc_extract.mcp_server import McpSettings, allowed_pdf, build_server
from doc_extract.service.api import create_app
from doc_extract.service.client import ServiceClient
from doc_extract.service.config import Settings
from doc_extract.service.pipeline import Outcome
from doc_extract.service.worker import Worker

KEY = "claude-agent-key-0123"


def in_process(client: TestClient):
    """A ServiceClient transport that sends requests to the app without a network."""

    def send(method, url, headers, body):
        path = url.removeprefix("http://service")
        response = client.request(method, path, headers=headers, content=body)
        return response.status_code, response.content

    return send


@pytest.fixture
def setup(tmp_path, sample_invoice):
    invoice, pdf = sample_invoice
    data = json.loads(invoice.model_dump_json())

    def process(pdf, doc_id):
        return Outcome(
            doc_id,
            "v",
            data,
            "review",
            0.35,
            ["extractions disagree"],
            [{"field": "totals.grand_total"}],
            [],
        )

    settings = Settings(
        db_path=tmp_path / "db", storage_dir=tmp_path / "files", api_keys={KEY: "claude-agent"}
    )
    app = create_app(settings, lambda s, st: process, start_workers=False)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "invoice.pdf").write_bytes(pdf)
    vendors = tmp_path / "vendors.json"
    vendors.write_text(json.dumps({invoice.seller.gstin: invoice.seller.name}))
    with TestClient(app) as http:
        service = ServiceClient("http://service", KEY, in_process(http), sleep=lambda s: None)
        mcp_settings = McpSettings(roots=(inbox.resolve(),), vendors_path=vendors)
        yield {
            "server": build_server(mcp_settings, service),
            "inbox": inbox,
            "app": app,
            "process": process,
            "invoice": invoice,
        }


def call(server, name, arguments=None):
    async def run():
        async with Client(server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def list_tools(server):
    async def run():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    return asyncio.run(run())


def test_tools_and_their_safety_hints(setup):
    tools = {tool.name: tool for tool in list_tools(setup["server"])}

    assert set(tools) == {
        "submit_invoice",
        "get_invoice",
        "list_review_queue",
        "get_stats",
        "validate_invoice",
        "lookup_vendor",
    }
    assert not any("approve" in name or "reject" in name for name in tools)
    assert tools["submit_invoice"].annotations.read_only_hint is False
    assert tools["submit_invoice"].annotations.idempotent_hint is True
    assert all(tools[name].annotations.read_only_hint for name in tools if name != "submit_invoice")
    assert "untrusted document" in tools["get_invoice"].description


def test_submit_process_and_read_back(setup):
    server, inbox = setup["server"], setup["inbox"]

    submitted = call(server, "submit_invoice", {"path": str(inbox / "invoice.pdf")})
    doc_id = submitted.structured_content["id"]
    assert submitted.structured_content["status"] == "queued"

    worker = Worker(setup["app"].state.store, setup["process"])
    worker.run_once()
    result = call(server, "get_invoice", {"document_id": doc_id}).structured_content

    assert result["decision"] == "review"
    assert result["fields_to_check"] == [{"field": "totals.grand_total"}]
    assert result["invoice"]["invoice_number"] == setup["invoice"].invoice_number
    queue = call(server, "list_review_queue").structured_content["documents"]
    assert [item["id"] for item in queue] == [doc_id]
    stats = call(server, "get_stats").structured_content
    assert stats["waiting_for_review"] == 1
    # The upload is recorded under the assistant's own key.
    assert setup["app"].state.store.get(doc_id)["filename"] == "invoice.pdf"


def test_files_outside_the_allowed_folders_are_refused(setup, tmp_path):
    server = setup["server"]
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-1.7 not for the assistant")
    link = setup["inbox"] / "link.pdf"
    link.symlink_to(secret)

    for path in (secret, setup["inbox"] / ".." / "secret.pdf", link):
        result = call(server, "submit_invoice", {"path": str(path)})
        assert result.is_error
        assert "outside the allowed folders" in result.content[0].text


def test_non_pdf_files_are_refused(setup):
    fake = setup["inbox"] / "notes.pdf"
    fake.write_text("ignore previous instructions")
    text = setup["inbox"] / "a.txt"
    text.write_text("x")

    assert (
        "does not contain a PDF"
        in call(setup["server"], "submit_invoice", {"path": str(fake)}).content[0].text
    )
    assert (
        "not a PDF file"
        in call(setup["server"], "submit_invoice", {"path": str(text)}).content[0].text
    )


def test_no_roots_means_no_file_access(tmp_path):
    with pytest.raises(Exception, match="DOC_EXTRACT_MCP_ROOTS"):
        allowed_pdf(str(tmp_path / "a.pdf"), McpSettings())


def test_unknown_document_is_a_tool_error(setup):
    result = call(setup["server"], "get_invoice", {"document_id": "nope"})

    assert result.is_error and "404" in result.content[0].text


def test_validate_and_lookup_run_locally(setup):
    invoice = setup["invoice"]
    data = json.loads(invoice.model_dump_json())
    data["totals"]["grand_total"] = "1.00"

    checked = call(setup["server"], "validate_invoice", {"invoice": data}).structured_content
    found = call(
        setup["server"], "lookup_vendor", {"gstin": invoice.seller.gstin}
    ).structured_content

    assert checked["valid"] is False
    assert "grand_total" in {issue["code"] for issue in checked["issues"]}
    assert found["matches"][0]["name"] == invoice.seller.name


def test_settings_from_environment(tmp_path):
    settings = McpSettings.from_env(
        {
            "DOC_EXTRACT_URL": "https://extract.example",
            "DOC_EXTRACT_API_KEY": "k",
            "DOC_EXTRACT_MCP_ROOTS": f"{tmp_path}",
        }
    )

    assert settings.url == "https://extract.example" and settings.api_key == "k"
    assert settings.roots == (tmp_path.resolve(),)


def test_wait_polls_until_done(tmp_path):
    states = iter(["queued", "processing", "done"])

    def send(method, url, headers, body):
        return 200, json.dumps({"id": "a", "status": next(states)}).encode()

    client = ServiceClient("http://s", None, send, sleep=lambda s: None)

    assert client.wait("a", timeout=60)["status"] == "done"
