"""LLM providers behind one small interface: a prompt, optional page images, and a
JSON schema in; JSON text, token counts, and timing out.

Plain HTTP, no SDKs, so the request each provider receives is visible in one place.
Every completion records its provider and model, so results from different models
are never mixed silently.
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

Post = Callable[[str, dict, dict[str, str], float], dict]

_RETRY_STATUSES = {429, 500, 503}


class LLMError(RuntimeError):
    pass


class RetryableError(LLMError):
    """Rate limits and overloads, which are worth waiting for."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    seconds: float


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(
        self, prompt: str, schema: dict[str, Any], images: Sequence[bytes] = ()
    ) -> Completion: ...


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    host = url.split("/")[2]
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in _RETRY_STATUSES:
            retry_after = error.headers.get("Retry-After")
            raise RetryableError(
                f"{host} returned HTTP {error.code}",
                float(retry_after) if retry_after and retry_after.isdigit() else None,
            ) from error
        raise LLMError(f"{host} returned HTTP {error.code}: {error.read()[:300]!r}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise LLMError(f"could not reach {host}: {error}") from error


def with_retries(
    call: Callable[[], dict],
    attempts: int = 4,
    base_delay: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Retry rate limits and overloads with exponential backoff, honouring Retry-After."""
    for attempt in range(attempts):
        try:
            return call()
        except RetryableError as error:
            if attempt == attempts - 1:
                raise
            sleep(error.retry_after or base_delay * 2**attempt)
    raise AssertionError("unreachable")


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash-lite",
        base_url: str = "https://generativelanguage.googleapis.com",
        post: Post = post_json,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 120,
    ) -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url
        self._post, self._sleep, self._timeout = post, sleep, timeout

    def generate(
        self, prompt: str, schema: dict[str, Any], images: Sequence[bytes] = ()
    ) -> Completion:
        parts: list[dict] = [{"text": prompt}]
        parts += [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode()}}
            for image in images
        ]
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        started = time.perf_counter()
        body = with_retries(
            lambda: self._post(url, payload, headers, self._timeout), sleep=self._sleep
        )
        seconds = time.perf_counter() - started
        try:
            candidate = body["candidates"][0]
            text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
        except (KeyError, IndexError) as error:
            reason = body.get("promptFeedback") or body.get("candidates")
            raise LLMError(f"Gemini returned no text: {reason}") from error
        usage = body.get("usageMetadata", {})
        # Thinking models bill their reasoning tokens as output, so count them too.
        output = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        return Completion(
            text,
            self.name,
            body.get("modelVersion", self.model),
            usage.get("promptTokenCount"),
            output or None,
            seconds,
        )


class OllamaProvider:
    """Local models. Nothing leaves the machine, at the cost of speed on a CPU."""

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5vl:3b",
        base_url: str = "http://localhost:11434",
        post: Post = post_json,
        timeout: float = 900,
    ) -> None:
        self.model, self.base_url = model, base_url
        self._post, self._timeout = post, timeout

    def generate(
        self, prompt: str, schema: dict[str, Any], images: Sequence[bytes] = ()
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        if images:
            payload["images"] = [base64.b64encode(image).decode() for image in images]
        started = time.perf_counter()
        body = self._post(f"{self.base_url}/api/generate", payload, {}, self._timeout)
        return Completion(
            body["response"],
            self.name,
            self.model,
            body.get("prompt_eval_count"),
            body.get("eval_count"),
            time.perf_counter() - started,
        )


def build_provider(choice: str) -> LLMProvider:
    if choice == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMError(
                "Set GEMINI_API_KEY (free at Google AI Studio), or use --provider ollama"
            )
        return GeminiProvider(api_key, os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    if choice == "ollama":
        return OllamaProvider(
            os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b"),
            os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    raise LLMError(f"unknown provider {choice!r}")
