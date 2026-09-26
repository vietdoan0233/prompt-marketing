"""Company-website enrichment crawler (C-tier).

Targets are domains that an approved registry already published for a company (never arbitrary URLs).
Per domain: robots.txt respected, 1 request/second per host, at most CRAWL_MAX_PAGES_PER_DOMAIN pages,
HTML only. The raw HTML is not stored; the snapshot keeps page URLs, HTTP status and extracted evidence.
Records are enrichment-only: they attach to an existing company or are rejected; they never create one.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.connectors.http import PoliteClient
from app.domain import website_extract as wx
from app.domain.records import PARSER_VERSION


class WebsiteConnector:
    parser_version = f"website-{wx.EXTRACTION_VERSION}-{PARSER_VERSION}"

    def __init__(self, rate_limit_per_minute: int | None = 60) -> None:
        self.client = PoliteClient(rate_limit_per_minute or 60, respect_robots=True)
        self.max_pages = get_settings().crawl_max_pages_per_domain

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        targets = query.get("targets") or []
        if not targets:
            raise ConnectorError("no target domains selected (targets come from registry-published websites)")
        return [CandidateRef(t["domain"], f"https://{t['domain']}", t) for t in targets]

    def fetch_many(self, refs: list[CandidateRef]) -> tuple[list[FetchedSnapshot], list[dict[str, str]]]:
        snapshots: list[FetchedSnapshot] = []
        errors: list[dict[str, str]] = []

        def one(ref: CandidateRef) -> FetchedSnapshot | dict[str, str]:
            try:
                return self.fetch(ref)
            except ConnectorError as exc:
                return {"stage": "fetch", "reference": ref.reference, "message": str(exc)}

        with ThreadPoolExecutor(max_workers=get_settings().crawl_workers) as pool:
            for result in pool.map(one, refs):
                if isinstance(result, FetchedSnapshot):
                    snapshots.append(result)
                else:
                    errors.append(result)
        return snapshots, errors

    def _get_page(self, url: str, domain: str) -> dict[str, Any] | None:
        try:
            resp = self.client.request("GET", url)
        except ConnectorError as exc:
            return {"url": url, "status": None, "error": str(exc)}
        if resp.status >= 400 or "html" not in resp.content_type.lower():
            return {
                "url": resp.url,
                "status": resp.status,
                "error": f"HTTP {resp.status} {resp.content_type[:40]}",
            }
        text, links = wx.html_to_text_and_links(resp.text, resp.url)
        return {"url": resp.url, "status": resp.status, "text": text, "links": links}

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot:
        domain = ref.reference
        country = ref.hint.get("country", "")
        home = self._get_page(f"https://{domain}/", domain)
        if home is None or home.get("error"):
            home = self._get_page(f"https://www.{domain}/", domain)
        if home is None or home.get("error"):
            raise ConnectorError(f"homepage not retrievable: {(home or {}).get('error', 'unknown error')}")
        final_host = (urlsplit(home["url"]).hostname or domain).lower().removeprefix("www.")
        pages: dict[str, dict[str, Any]] = {"home": home}
        targets = wx.categorize_links(home["links"], final_host)
        for category, paths in wx.FALLBACK_PATHS.get(country, {}).items():
            if category not in targets:
                targets[category] = f"https://{urlsplit(home['url']).netloc}{paths[0]}"
        for category in ("impressum", "about", "team", "careers"):
            if len(pages) >= self.max_pages or category not in targets:
                continue
            page = self._get_page(targets[category], final_host)
            if page and not page.get("error"):
                pages[category] = page
        evidence = {
            "domain": domain,
            "final_host": final_host,
            "country": country,
            "pages": {
                k: {
                    "url": v["url"],
                    "status": v["status"],
                    "text": v.get("text", ""),
                    "links": v.get("links", []),
                }
                for k, v in pages.items()
            },
        }
        return FetchedSnapshot(domain, evidence, home["url"], home["status"], request_id=f"crawl:{domain}")

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        ev = snapshot.payload
        domain, country, pages = ev["domain"], ev["country"], ev["pages"]
        all_text = "\n".join(p.get("text", "") for p in pages.values())
        row: dict[str, Any] = {
            "source_key": f"web:{domain}",
            "country": country,
            "website": domain,
            "enrichment_only": True,
            "estimated_fields": ["open_positions", "founder_signal", "family_business_signal"],
            "evidence": {},
        }
        page_url = {k: v["url"] for k, v in pages.items()}

        imp = pages.get("impressum")
        id_text = imp["text"] if imp else all_text
        regs = wx.extract_registry_ids(id_text, country)
        if len(regs) == 1:  # several different IDs (group site) are ambiguous: store none
            row["registry_id"] = (
                regs[0].split(":", 1)[1].replace("CHE", "CHE-", 1)
                if country == "CH"
                else regs[0].split(":", 1)[1]
            )
            if country == "DE":
                _, number, court = regs[0].split(":")
                row["registry_id"] = f"{number[:3]} {number[3:]}, Amtsgericht {court.title()}"
            row["evidence"]["registry_id"] = page_url.get("impressum", page_url["home"])
        elif len(regs) > 1:
            row["warnings"] = [f"page states several registry IDs {regs}; none stored"]
        vats = wx.extract_vat_ids(id_text, country)
        if len(vats) == 1:
            row["vat_id"] = vats[0]
            row["evidence"]["vat_id"] = page_url.get("impressum", page_url["home"])

        if imp and country in ("DE", "AT", "CH"):
            name = wx.extract_legal_name(imp["text"])
            if name:
                row["legal_name"] = name
                row["evidence"]["legal_name"] = imp["url"]
            row["contacts"] = [
                {
                    "contact_name": nm,
                    "contact_role": role,
                    "contact_basis": "public-business",
                    "source_url": imp["url"],
                }
                for nm, role in wx.extract_managing_directors(imp["text"])
            ]

        careers = pages.get("careers")
        if careers:
            count, ats = wx.count_job_postings(careers["links"], careers["url"], ev["final_host"])
            if count is not None:
                row["open_positions"] = str(count)
                row["evidence"]["open_positions"] = careers["url"]
            if ats:
                row.setdefault("warnings", []).append(
                    f"job postings hosted on external ATS {ats} (not crawled)"
                )

        about_text = "\n".join(pages[k]["text"] for k in ("about", "team", "home") if k in pages)
        if wx.has_any(about_text, wx.FOUNDER_WORDS):
            row["founder_signal"] = "true"
            row["evidence"]["founder_signal"] = page_url.get("about", page_url.get("team", page_url["home"]))
        if wx.has_any(about_text, wx.FAMILY_WORDS):
            row["family_business_signal"] = "true"
            row["evidence"]["family_business_signal"] = page_url.get("about", page_url["home"])
        row["source_url"] = page_url.get("impressum", page_url["home"])
        return [row]
