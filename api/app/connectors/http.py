"""Polite HTTP client for official Estonian portal requests.

- Stable User-Agent; optional contact via HTTP_CONTACT (never personal by default).
- Per-host minimum interval (derived from the source's rate_limit_per_minute) + retry with backoff on 429/5xx.
- Response size cap so a single page cannot exhaust memory.
- Optional robots.txt compliance (website connectors).
"""

import ssl
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
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
KEPT_HEADERS = {"last-modified", "content-type", "etag", "date"}


def user_agent() -> str:
    contact = get_settings().http_contact.strip()
    suffix = f"; contact: {contact}" if contact else ""
    return f"Mergero-DataPipeline/0.2 (internal company-data research{suffix})"


def _is_tls_error(exc: BaseException) -> bool:
    """True when a connection failed on TLS (e.g. invalid, expired or self-signed certificate)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLError):
            return True
        text = str(cur).upper()
        if "CERTIFICATE_VERIFY_FAILED" in text or "SSL" in text or "CERTIFICATE" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class PoliteClient:
    def __init__(self, rate_limit_per_minute: int | None = 60, respect_robots: bool = False) -> None:
        settings = get_settings()
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
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

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(parts.netloc)
                resp = self._client.get(f"{origin}/robots.txt")
                if resp.status_code >= 400:
                    rp = None  # no robots.txt: crawling allowed
                else:
                    rp.parse(resp.text.splitlines())  # type: ignore[union-attr]
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(user_agent(), url)

    def request(self, method: str, url: str, *, retries: int = 3, **kwargs) -> Response:
        """`retries` is the total number of attempts (default 3); probes of guessed hosts pass 1."""
        if not self.allowed(url):
            raise ConnectorError(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc
        attempts = max(1, retries)
        last = attempts - 1
        for attempt in range(attempts):
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
                    headers = {k.lower(): v for k, v in resp.headers.items() if k.lower() in KEPT_HEADERS}
                    final_url = str(resp.url)
                    encoding = resp.encoding or "utf-8"
            except httpx.HTTPError as exc:
                if attempt == last:
                    if _is_tls_error(exc):
                        raise ConnectorError(
                            f"{method} {url} failed: {type(exc).__name__} (ssl certificate error)"
                        ) from exc
                    raise ConnectorError(f"{method} {url} failed: {type(exc).__name__}") from exc
                time.sleep(2**attempt)
                continue
            if status in (429, 502, 503, 504) and attempt < last:
                time.sleep(2 ** (attempt + 1))
                continue
            return Response(final_url, status, content_type, body.decode(encoding, errors="replace"), headers)
        raise ConnectorError(f"{method} {url} failed after retries")

    def get_json(self, url: str, **kwargs):
        resp = self.request("GET", url, headers={"Accept": "application/json"}, **kwargs)
        if resp.status >= 400:
            raise ConnectorError(f"GET {url} returned HTTP {resp.status}")
        import json

        return json.loads(resp.text)
