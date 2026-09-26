"""Polite HTTP client for official Estonian portal requests.

- Stable User-Agent; optional contact via HTTP_CONTACT (never personal by default).
- Per-host minimum interval (derived from the source's rate_limit_per_minute) + retry with backoff on 429/5xx.
- Response size cap so a single page cannot exhaust memory.
"""

import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

try:  # OS trust store (handles servers with incomplete certificate chains); certifi fallback
    import truststore

    _SSL_CONTEXT: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:  # pragma: no cover
    _SSL_CONTEXT = True

from app.config import get_settings
from app.connectors.base import ConnectorError

MAX_BYTES = 2_000_000


def user_agent() -> str:
    contact = get_settings().http_contact.strip()
    suffix = f"; contact: {contact}" if contact else ""
    return f"Mergero-DataPipeline/0.2 (internal company-data research{suffix})"


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    text: str


class PoliteClient:
    def __init__(self, rate_limit_per_minute: int | None = 60) -> None:
        settings = get_settings()
        self.min_interval = 60.0 / rate_limit_per_minute if rate_limit_per_minute else 0.0
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            verify=_SSL_CONTEXT,
            headers={"User-Agent": user_agent(), "Accept-Language": "et;q=0.8, *;q=0.5"},
        )

    def close(self) -> None:
        self._client.close()

    def _throttle(self, host: str) -> None:
        if not self.min_interval:
            return
        with self._lock:
            wait = self._last.get(host, 0.0) + self.min_interval - time.monotonic()
            self._last[host] = max(time.monotonic(), self._last.get(host, 0.0) + self.min_interval)
        if wait > 0:
            time.sleep(wait)

    def request(self, method: str, url: str, **kwargs) -> Response:
        host = urlsplit(url).netloc
        for attempt in range(3):
            self._throttle(host)
            try:
                with self._client.stream(method, url, **kwargs) as resp:
                    chunks, size = [], 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            break
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    status = resp.status_code
                    content_type = resp.headers.get("content-type", "")
                    final_url = str(resp.url)
                    encoding = resp.encoding or "utf-8"
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise ConnectorError(f"{method} {url} failed: {type(exc).__name__}") from exc
                time.sleep(2**attempt)
                continue
            if status in (429, 502, 503, 504) and attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            return Response(final_url, status, content_type, body.decode(encoding, errors="replace"))
        raise ConnectorError(f"{method} {url} failed after retries")

    def get_json(self, url: str, **kwargs):
        resp = self.request("GET", url, headers={"Accept": "application/json"}, **kwargs)
        if resp.status >= 400:
            raise ConnectorError(f"GET {url} returned HTTP {resp.status}")
        import json

        return json.loads(resp.text)
