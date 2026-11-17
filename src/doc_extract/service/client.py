"""A small client for the HTTP API, used by the MCP server and by scripts.

Plain urllib, like the LLM clients, so every request is visible in one place. The
transport is injectable: tests pass one that calls the app in process, so the
client is tested against the real API rather than a mock of it.
"""

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

# (method, url, headers, body) -> (status, response body)
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class ServiceError(RuntimeError):
    def __init__(self, status: int, detail: object) -> None:
        super().__init__(f"service returned {status}: {detail}")
        self.status, self.detail = status, detail


def urllib_transport(timeout: float = 60) -> Transport:
    def send(method: str, url: str, headers: dict[str, str], body: bytes | None):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise ServiceError(0, f"could not reach {url}: {error}") from error

    return send


class ServiceClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self._send = transport or urllib_transport()
        self._sleep = sleep

    def _request(self, method: str, path: str, body: bytes | None = None, headers=None) -> object:
        status, raw = self._send(
            method, self.base_url + path, {**self.headers, **(headers or {})}, body
        )
        try:
            data = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            data = raw[:300].decode(errors="replace")
        if status >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            raise ServiceError(status, detail)
        return data

    def upload(self, pdf: bytes, filename: str) -> dict:
        boundary = uuid.uuid4().hex
        safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
                "Content-Type: application/pdf\r\n\r\n"
            ).encode()
            + pdf
            + f"\r\n--{boundary}--\r\n".encode()
        )
        content_type = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        return self._request("POST", "/v1/documents", body, content_type)

    def document(self, doc_id: str) -> dict:
        return self._request("GET", f"/v1/documents/{urllib.request.quote(doc_id, safe='')}")

    def wait(self, doc_id: str, timeout: float, interval: float = 2.0) -> dict:
        """Poll until the document is done or failed, or the timeout passes."""
        deadline = time.monotonic() + timeout
        while True:
            document = self.document(doc_id)
            if document["status"] in ("done", "failed") or time.monotonic() >= deadline:
                return document
            self._sleep(interval)

    def review_queue(self, limit: int = 10) -> list[dict]:
        return self._request("GET", f"/v1/review-queue?limit={int(limit)}")

    def stats(self) -> dict:
        return self._request("GET", "/v1/stats")
