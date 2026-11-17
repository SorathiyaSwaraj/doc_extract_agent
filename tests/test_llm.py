import base64

import pytest

from doc_extract.extraction.llm import (
    GeminiProvider,
    LLMError,
    OllamaProvider,
    RetryableError,
    build_provider,
    with_retries,
)

SCHEMA = {"type": "object", "properties": {}}


class RecordingPost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append((url, payload, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


GEMINI_OK = {
    "candidates": [{"content": {"parts": [{"text": '{"a": '}, {"text": "1}"}]}}],
    "usageMetadata": {
        "promptTokenCount": 1200,
        "candidatesTokenCount": 90,
        "thoughtsTokenCount": 10,
    },
    "modelVersion": "gemini-test-001",
}


def test_gemini_sends_schema_image_and_key_header():
    post = RecordingPost(GEMINI_OK)
    provider = GeminiProvider("secret", model="m", post=post)

    completion = provider.generate("Extract.", SCHEMA, images=[b"png-bytes"])

    url, payload, headers = post.calls[0]
    assert url.endswith("/v1beta/models/m:generateContent")
    assert headers == {"x-goog-api-key": "secret"}
    assert "secret" not in url
    config = payload["generationConfig"]
    assert config["responseJsonSchema"] == SCHEMA
    assert config["responseMimeType"] == "application/json"
    assert config["temperature"] == 0
    image = payload["contents"][0]["parts"][1]["inline_data"]
    assert base64.b64decode(image["data"]) == b"png-bytes"
    assert completion.text == '{"a": 1}'
    assert (completion.input_tokens, completion.output_tokens) == (1200, 100)
    assert completion.model == "gemini-test-001"


def test_gemini_reports_blocked_responses():
    post = RecordingPost({"promptFeedback": {"blockReason": "OTHER"}})

    with pytest.raises(LLMError, match="blockReason"):
        GeminiProvider("k", post=post).generate("x", SCHEMA)


def test_gemini_waits_out_rate_limits():
    post = RecordingPost(RetryableError("429", retry_after=7), RetryableError("503"), GEMINI_OK)
    waits = []

    GeminiProvider("k", post=post, sleep=waits.append).generate("x", SCHEMA)

    assert waits == [7, 10.0]


def test_retries_give_up_eventually():
    def always_limited():
        raise RetryableError("429")

    with pytest.raises(RetryableError):
        with_retries(always_limited, attempts=3, sleep=lambda _: None)


def test_ollama_sends_schema_as_format_and_images():
    post = RecordingPost({"response": "{}", "prompt_eval_count": 50, "eval_count": 5})

    completion = OllamaProvider("local", post=post).generate("x", SCHEMA, images=[b"img"])

    url, payload, _ = post.calls[0]
    assert url.endswith("/api/generate")
    assert payload["format"] == SCHEMA
    assert payload["images"] == [base64.b64encode(b"img").decode()]
    assert (completion.provider, completion.model, completion.output_tokens) == (
        "ollama",
        "local",
        5,
    )


def test_gemini_needs_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        build_provider("gemini")
