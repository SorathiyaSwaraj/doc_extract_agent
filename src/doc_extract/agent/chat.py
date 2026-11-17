"""Multi-turn chat with tool calls, for the agent loop.

The model's turns go back into the history unchanged. Gemini 3 attaches a
thought signature to each function call and rejects a history where it is
missing, so the history must never be rebuilt from parsed pieces.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from doc_extract.extraction.llm import LLMError, Post, post_json, with_retries


@dataclass(frozen=True)
class Turn:
    content: dict  # the model's message, exactly as returned
    input_tokens: int
    output_tokens: int
    seconds: float
    model: str

    @property
    def calls(self) -> list[dict]:
        return [
            part["functionCall"] for part in self.content.get("parts", []) if "functionCall" in part
        ]


class ChatModel(Protocol):
    name: str
    model: str

    def turn(self, contents: list[dict], tools: list[dict], system: str) -> Turn: ...


class GeminiChat:
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

    def turn(self, contents: list[dict], tools: list[dict], system: str) -> Turn:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "tools": [{"functionDeclarations": tools}],
            "generationConfig": {"temperature": 0},
        }
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        started = time.perf_counter()
        body = with_retries(
            lambda: self._post(url, payload, {"x-goog-api-key": self.api_key}, self._timeout),
            sleep=self._sleep,
        )
        try:
            content = body["candidates"][0]["content"]
        except (KeyError, IndexError) as error:
            raise LLMError(f"Gemini returned no message: {body.get('promptFeedback')}") from error
        usage = body.get("usageMetadata", {})
        return Turn(
            content,
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            time.perf_counter() - started,
            body.get("modelVersion", self.model),
        )
