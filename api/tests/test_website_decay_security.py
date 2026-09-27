"""Website Digital Decay crawler safety:

1. `PoliteClient` (app.connectors.http) blocks private, loopback, link-local, reserved and metadata-service
   network targets before connecting -- for a literal IP, a hostname that resolves to one, and every
   redirect hop -- and pins the connection to the exact address it validated, so a second, independent DNS
   lookup at connect time (DNS rebinding) can never substitute a different one.
2. A caller-supplied domain (`WebsiteDecayConnector`, app.connectors.website_decay) is marked verified only
   when the on-page company-identity check actually succeeds, never just because it was supplied.
"""

import json
import socket

import httpx
import pytest

from app.connectors.base import CandidateRef, ConnectorError
from app.connectors.http import PoliteClient
from app.connectors.website_decay import WebsiteDecayConnector

REAL_GETADDRINFO = socket.getaddrinfo
REGISTRY_CODE = "12345678"
HINT = {
    "company_id": "c1",
    "registry_code": REGISTRY_CODE,
    "legal_name": "Example OÜ",
    "postal_code": "10111",
    "street": "Narva mnt 5",
    "domain": "example.test",
    "registry_domains": {},
    "revenue": None,
    "headcount": None,
}


# ------------------------------------------------------------------ SSRF / DNS rebinding


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://10.1.2.3/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"],
)
def test_literal_private_loopback_link_local_and_metadata_addresses_are_blocked(url: str) -> None:
    client = PoliteClient(rate_limit_per_minute=None)
    try:
        with pytest.raises(ConnectorError, match="blocked network address"):
            client.request("GET", url)
    finally:
        client.close()


def test_hostname_resolving_to_a_private_address_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host == "attacker.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
        return REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    client = PoliteClient(rate_limit_per_minute=None)
    try:
        with pytest.raises(ConnectorError, match="blocked network address"):
            client.request("GET", "http://attacker.example/")
    finally:
        client.close()


def test_shared_carrier_nat_address_is_blocked_without_opening_a_network_connection() -> None:
    client = PoliteClient(
        rate_limit_per_minute=None,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="unexpected request")),
    )
    try:
        with pytest.raises(ConnectorError, match="blocked network address"):
            client.request("GET", "http://100.64.0.1/")
    finally:
        client.close()


def test_redirect_target_is_revalidated_and_connection_is_pinned_to_the_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["header_host"] = request.headers.get("host")
        seen["sni"] = request.extensions.get("sni_hostname")
        if request.url.host == "93.184.216.34" and "redirected" not in seen:
            seen["redirected"] = True
            return httpx.Response(302, headers={"location": "http://attacker.example/evil"})
        return httpx.Response(200, text="ok")

    def fake_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host == "good.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        if host == "attacker.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        return REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    client = PoliteClient(rate_limit_per_minute=None, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ConnectorError, match="blocked network address"):
            client.request("GET", "http://good.example/")
    finally:
        client.close()
    # The first hop connected to the validated address, not a re-resolved one, while still sending the
    # real hostname as the Host header and TLS SNI name (so cert/vhost checks still target the real site).
    assert seen["host"] == "93.184.216.34"
    assert seen["header_host"] == "good.example"
    assert seen["sni"] == "good.example"


def test_legitimate_public_target_still_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    def fake_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host == "good.example":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        return REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    client = PoliteClient(rate_limit_per_minute=None, transport=httpx.MockTransport(handler))
    try:
        resp = client.request("GET", "http://good.example/")
    finally:
        client.close()
    assert resp.status == 200


# ------------------------------------------------------------------ caller-supplied-domain identity


def _home_html(identity_present: bool) -> str:
    body = f"Registry code: {REGISTRY_CODE}" if identity_present else "Nothing relevant on this page."
    return f"<html><body><p>{body}</p></body></html>"


def _connector(monkeypatch: pytest.MonkeyPatch, identity_present: bool) -> WebsiteDecayConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text=_home_html(identity_present)
            )
        return httpx.Response(404, text="not found")

    # example.test (RFC 2606) never resolves for real; the SSRF-safe transport still resolves and
    # validates DNS for it before handing off to the mocked inner transport above.
    def fake_getaddrinfo(host: str, *args: object, **kwargs: object):
        if host in ("example.test", "www.example.test"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        return REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    client = PoliteClient(rate_limit_per_minute=None, transport=httpx.MockTransport(handler))
    return WebsiteDecayConnector(client=client)


def _fetch_and_parse(connector: WebsiteDecayConnector) -> tuple[dict, dict]:
    ref = CandidateRef(reference="web-decay:test", hint=dict(HINT))
    snapshot = connector.fetch(ref)
    rows = connector.parse(snapshot)
    return rows[0], json.loads(rows[0]["digital_decay_signal"])


def test_caller_supplied_domain_is_verified_only_when_identity_check_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, signal = _fetch_and_parse(_connector(monkeypatch, identity_present=True))
    assert signal["domain_verification"] == "registry_code"
    assert row.get("website_verified") is True
    assert row.get("website") == "example.test"


def test_caller_supplied_domain_is_not_verified_when_identity_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, signal = _fetch_and_parse(_connector(monkeypatch, identity_present=False))
    assert signal["domain_verification"] == "unverified"
    assert "website_verified" not in row
    assert "website" not in row
