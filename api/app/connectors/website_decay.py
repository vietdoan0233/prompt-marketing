"""Website digital-decay connector (enrichment only) for Estonian companies already in the database.

For each selected target it finds the company's own website in this order: a user-supplied domain; the
website the company declared to the official register; the company email domain declared to the register
(group mail domains only when the site itself names the company); a domain guessed from the legal name,
verified on the site (registry code, or name plus address; never the name alone). It then politely
crawls a handful of pages (per-host rate limit, page budget) and records three decay
checks as evidence. Scoring is pure (app.domain.digital_decay) and runs in parse(), from the payload only,
so re-parsing a stored snapshot is deterministic.

The payload holds evidence only: URLs, dates, counts and at most two short matched phrases. Raw HTML,
page text, emails and phone numbers never leave fetch().
"""

import json
import re
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.connectors.http import PoliteClient
from app.domain import digital_decay as dd
from app.domain import normalize as n

MAX_TOTAL_PAGES = 20  # hard cap on HTML requests per company, candidate and register-domain probes included
EVIDENCE_ERROR_CHARS = 120
TLS_ERROR_MARKER = "ssl certificate error"
MAX_SITEMAPS = 3  # sitemap requests per company, index and fallbacks included
MAX_SITEMAP_CHILDREN = 2
SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")
MAX_PAYLOAD_POSTS = 60
ESTIMATED_FIELDS = [
    "website",
    "footer_copyright_year",
    "latest_news_date",
    "open_positions",
    "digital_decay_signal",
]


def signal_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["digital_decay_signal"])


def _is_html(page: dict[str, Any]) -> bool:
    ctype = (page.get("content_type") or "").lower()
    if ctype:
        return "html" in ctype
    return "<html" in (page.get("html") or "")[:2000].lower()


def _ok(page: dict[str, Any] | None) -> bool:
    return bool(page) and page.get("status") is not None and page.get("error") is None  # type: ignore[union-attr]


def _http_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(snippet: str) -> str:
    """Matched phrases are the only text kept: drop anything email- or ID-number-like, cap length."""
    return re.sub(r"\d{6,}", "", snippet.replace("@", "")).strip()[:80]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class _Crawl:
    """Per-company fetch state: page budget, fetched-page cache, page log and warnings."""

    def __init__(self, client: PoliteClient, budget: int) -> None:
        self.client = client
        self.budget = budget
        self.sitemaps_left = MAX_SITEMAPS
        self.cache: dict[str, dict[str, Any]] = {}
        self.pages: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def get(
        self, url: str, *, probe: bool = False, sitemap: bool = False, quiet: bool = False
    ) -> dict[str, Any] | None:
        """GET one URL. Returns {"url","status","html","last_modified","content_type"} on success or
        {"url","status","error"} on failure; None when the budget is spent. HTML stays in memory."""
        if url in self.cache:
            return self.cache[url]
        if sitemap:
            if self.sitemaps_left <= 0:
                return None
            self.sitemaps_left -= 1
        else:
            if self.budget <= 0:
                return None
            self.budget -= 1
        page: dict[str, Any]
        try:
            resp = self.client.request("GET", url, retries=1) if probe else self.client.request("GET", url)
        except ConnectorError as exc:
            message = str(exc)
            page = {"url": url, "status": None, "error": message}
            if not quiet:
                self.warn(f"fetch failed: {url}")
        else:
            if resp.status >= 400:
                page = {"url": resp.url, "status": resp.status, "error": f"HTTP {resp.status}"}
                if not quiet:
                    self.warn(f"HTTP {resp.status}: {url}")
            else:
                page = {
                    "url": resp.url,
                    "status": resp.status,
                    "error": None,
                    "html": resp.text,
                    "last_modified": resp.headers.get("last-modified"),
                    "content_type": resp.content_type,
                }
        self.pages.append({"url": page["url"], "status": page["status"], "error": page["error"]})
        self.cache[url] = page
        return page


class WebsiteDecayConnector:
    parser_version = f"website-decay-{dd.DECAY_VERSION}"

    def __init__(
        self,
        rate_limit_per_minute: int | None = 20,
        *,
        today: date | None = None,
        thresholds: dd.Thresholds | None = None,
        max_pages: int = 8,
        client: PoliteClient | None = None,
    ) -> None:
        self.client = client or PoliteClient(rate_limit_per_minute or 20)
        self.today = today or datetime.now(UTC).date()
        self.thresholds = thresholds or dd.Thresholds()
        self.max_pages = max_pages

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ discover

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        targets = query.get("targets") or []
        if not targets:
            raise ConnectorError("no targets selected")
        refs = []
        for target in targets:
            key = target.get("registry_code") or target["company_id"]
            refs.append(CandidateRef(reference=f"web-decay:{key}", source_url=None, hint=dict(target)))
        return refs

    # ------------------------------------------------------------------ fetch

    def fetch_many(self, refs: list[CandidateRef]) -> tuple[list[FetchedSnapshot], list[dict[str, str]]]:
        snapshots: list[FetchedSnapshot] = []
        errors: list[dict[str, str]] = []
        for ref in refs:
            try:
                snapshots.append(self.fetch(ref))
            except ConnectorError as exc:
                errors.append({"stage": "fetch", "reference": ref.reference, "message": str(exc)})
        return snapshots, errors

    def _verify(self, text: str, hint: dict[str, Any]) -> str:
        return dd.verify_identity(
            text,
            registry_code=hint.get("registry_code"),
            legal_name=hint.get("legal_name") or "",
            postal_code=hint.get("postal_code"),
            street=hint.get("street"),
        )

    def _resolve_user_domain(
        self, crawl: _Crawl, hint: dict[str, Any], tried: list[str]
    ) -> tuple[str, dict[str, Any] | None, str]:
        domain = n.normalize_domain(hint["domain"])
        if not domain or n.is_generic_domain(domain):
            raise ConnectorError(f"invalid or generic website domain {hint['domain']!r}")
        tried.append(domain)
        home: dict[str, Any] | None = None
        for url in (f"https://{domain}/", f"https://www.{domain}/"):
            page = crawl.get(url)
            if _ok(page) and _is_html(page):  # type: ignore[arg-type]
                home = page
                break
        detail = "unverified"
        if home:
            text, links = dd.html_to_text_and_links(home["html"], home["url"])
            detail = self._verify(text, hint)
            if detail == "unverified":
                detail = self._verify_via_contact(crawl, hint, links, home["url"]) or detail
        else:
            crawl.warn("home page unreachable")
        return domain, home, detail

    def _verify_via_contact(
        self, crawl: _Crawl, hint: dict[str, Any], links: list[tuple[str, str]], home_url: str
    ) -> str | None:
        host = (urlsplit(home_url).hostname or "").removeprefix("www.")
        found = dd.find_section_links(links, host, {"contact": dd.CONTACT_KEYWORDS})
        url = found.get("contact") or f"{_origin(home_url)}/kontakt"
        page = crawl.get(url, quiet=True)
        if not (_ok(page) and _is_html(page)):  # type: ignore[arg-type]
            return None
        text, _ = dd.html_to_text_and_links(page["html"], page["url"])  # type: ignore[index]
        result = self._verify(text, hint)
        return None if result == "unverified" else result

    def _reach(self, crawl: _Crawl, domain: str) -> dict[str, Any] | None:
        """Home page of a register-listed domain: https, https+www, then http, http+www. A TLS failure that
        plain http gets past is reported as an informational warning."""
        tls_failed = False
        for url in (
            f"https://{domain}/",
            f"https://www.{domain}/",
            f"http://{domain}/",
            f"http://www.{domain}/",
        ):
            page = crawl.get(url, probe=True, quiet=True)
            if page is None:
                return None  # budget spent
            if _ok(page) and _is_html(page):
                if tls_failed and page["url"].startswith("http://"):
                    crawl.warn(f"TLS certificate invalid or expired on {domain}")
                return page
            if url.startswith("https://") and TLS_ERROR_MARKER in (page.get("error") or "").lower():
                tls_failed = True
        return None

    def _verify_page(self, crawl: _Crawl, hint: dict[str, Any], page: dict[str, Any]) -> str:
        text, links = dd.html_to_text_and_links(page["html"], page["url"])
        detail = self._verify(text, hint)
        if detail == "unverified":
            detail = self._verify_via_contact(crawl, hint, links, page["url"]) or detail
        return detail

    def _resolve_registry(
        self, crawl: _Crawl, hint: dict[str, Any], tried: list[str]
    ) -> tuple[str | None, dict[str, Any] | None, str, str | None]:
        """(final host, home page, domain_verification, verification_detail) from register-declared
        domains, or (None, None, "unverified", None)."""
        registry = hint.get("registry_domains") or {}
        www = [d for d in (n.normalize_domain(x) for x in registry.get("www") or []) if d]
        email = [d for d in (n.normalize_domain(x) for x in registry.get("email") or []) if d]
        shared = {n.normalize_domain(x) for x in registry.get("shared") or []}
        for domain in www:
            if n.is_generic_domain(domain) or domain in tried:
                continue
            tried.append(domain)
            page = self._reach(crawl, domain)
            host = (urlsplit(page["url"]).hostname or "").removeprefix("www.") if page else ""
            if page is None or not host:
                crawl.warn(f"register-listed website {domain} unreachable")
                continue
            if n.is_generic_domain(host):
                crawl.warn(f"register-listed website {domain} redirects to shared host {host}; not used")
                continue
            return host, page, "registry_www", self._verify_page(crawl, hint, page)
        for domain in email:
            if n.is_generic_domain(domain) or domain in tried:
                continue
            tried.append(domain)
            page = self._reach(crawl, domain)
            host = (urlsplit(page["url"]).hostname or "").removeprefix("www.") if page else ""
            if page is None or not host or n.is_generic_domain(host):
                continue
            detail = self._verify_page(crawl, hint, page)
            if domain in shared:
                if detail == "unverified":
                    crawl.warn(
                        f"email domain {domain} is shared by several register entries (group domain); "
                        "not used"
                    )
                    continue
                return host, page, detail, detail
            return host, page, "registry_email", detail
        return None, None, "unverified", None

    def _resolve_candidates(
        self, crawl: _Crawl, hint: dict[str, Any], candidates: list[str], tried: list[str]
    ) -> tuple[str | None, dict[str, Any] | None, str]:
        for cand in candidates:
            if cand in tried:
                continue
            tried.append(cand)
            page = crawl.get(f"https://{cand}/", probe=True, quiet=True)
            if page is None:
                break  # budget spent
            if page.get("status") is None:
                page = crawl.get(f"https://www.{cand}/", probe=True, quiet=True)  # connection error only
                if page is None:
                    break
            if not (_ok(page) and _is_html(page)):
                continue
            final_host = (urlsplit(page["url"]).hostname or "").removeprefix("www.")
            if not final_host or n.is_generic_domain(final_host):
                continue
            text, links = dd.html_to_text_and_links(page["html"], page["url"])
            verification = self._verify(text, hint)
            if verification == "unverified":
                verification = self._verify_via_contact(crawl, hint, links, page["url"]) or verification
            if verification != "unverified":
                return final_host, page, verification
        return None, None, "unverified"

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot:
        hint = ref.hint
        today = self.today
        tried: list[str] = []
        candidates = [] if hint.get("domain") else dd.domain_candidates(hint.get("legal_name") or "")
        registry = {} if hint.get("domain") else (hint.get("registry_domains") or {})
        registry_count = len(set(registry.get("www") or []) | set(registry.get("email") or []))
        crawl = _Crawl(
            self.client, min(MAX_TOTAL_PAGES, self.max_pages + len(candidates) + 2 * registry_count)
        )

        payload: dict[str, Any] = {
            "company_id": hint.get("company_id"),
            "registry_code": hint.get("registry_code"),
            "legal_name": hint.get("legal_name"),
            "domain": None,
            "final_host": None,
            "domain_verification": "unverified",
            "verification_detail": None,
            "domain_source": None,
            "domain_shared": False,
            "tried": tried,
            "home_url": None,
            "last_modified": {"header": None, "url": None},
            "copyright": {"year": None, "url": None, "snippet": None},
            "news": None,
            "sitemap": None,
            "careers": None,
            "pages": crawl.pages,
            "revenue": hint.get("revenue"),
            "headcount": hint.get("headcount"),
            "today": today.isoformat(),
            "warnings": crawl.warnings,
        }

        # 1. resolve and verify the domain
        detail: str | None
        if hint.get("domain"):
            domain, home, detail = self._resolve_user_domain(crawl, hint, tried)
            payload.update(
                domain=domain,
                # A caller-supplied domain is only ever marked verified when the company-identity check
                # on the page actually succeeds ("registry_code" or "name_and_address"); it is never
                # trusted just because the caller supplied it.
                domain_verification=detail,
                verification_detail=detail,
                domain_source="user",
            )
        else:
            found, home, verification, detail = self._resolve_registry(crawl, hint, tried)
            source = "registry_www" if verification == "registry_www" else "registry_email"
            if found is None or home is None:
                if not candidates:
                    crawl.warn("no domain candidates derivable from the legal name")
                found, home, verification = self._resolve_candidates(crawl, hint, candidates, tried)
                detail, source = verification, "name_guess"
            if found is None or home is None:
                crawl.warn("no verified company website found")
                return self._snapshot(ref, payload, None, None)
            shared = {n.normalize_domain(x) for x in registry.get("shared") or []}
            payload.update(
                domain=found,
                domain_verification=verification,
                verification_detail=detail,
                domain_source=source,
                domain_shared=bool(shared & {found, *tried[-1:]}),
            )
        if home is None:
            return self._snapshot(ref, payload, None, None)

        home_url = home["url"]
        final_host = (urlsplit(home_url).hostname or "").removeprefix("www.")
        origin = _origin(home_url)
        payload.update(home_url=home_url, final_host=final_host)

        # 2. home page evidence
        text, links = dd.html_to_text_and_links(home["html"], home_url)
        payload["last_modified"] = {"header": _http_date_to_iso(home.get("last_modified")), "url": home_url}
        hit = dd.copyright_match(dd.footer_text(home["html"]), text, today)
        if hit:
            payload["copyright"] = {"year": hit[0], "url": home_url, "snippet": _scrub(hit[1])}

        # 3. section links, with at most two fallback paths per missing category
        sections = dd.find_section_links(links, final_host)
        section_pages: dict[str, dict[str, Any]] = {}
        for cat in ("news", "careers"):
            if cat in sections:
                page = crawl.get(sections[cat])
                if _ok(page) and _is_html(page):  # type: ignore[arg-type]
                    section_pages[cat] = page  # type: ignore[assignment]
                continue
            for path in dd.FALLBACK_PATHS[cat][:2]:
                page = crawl.get(origin + path, quiet=True)
                if page is None:
                    break
                if _ok(page) and _is_html(page):
                    section_pages[cat] = page
                    break

        # 4. news page: its dates (cadence fallback) plus the first deeper post link (an on-page post date)
        page_best: tuple[date, str, str] | None = None
        page_posts: list[tuple[str, date, str]] = []
        listing_dates: list[tuple[date, str]] = []
        news_url: str | None = None
        news_page = section_pages.get("news")
        if news_page:
            news_url = news_page["url"]
            ntext, nlinks = dd.html_to_text_and_links(news_page["html"], news_url)
            listing_dates = dd.extract_dates(news_page["html"], ntext, today)
            top = dd.latest_date(listing_dates)
            if top:
                page_best = (top[0], top[1], news_url)
            news_path = urlsplit(news_url).path.rstrip("/")
            post_url = next(
                (
                    u
                    for u, _label in nlinks
                    if dd.same_site(u, final_host)
                    and urlsplit(u).path.rstrip("/").startswith(news_path + "/")
                    and not re.search(r"/(?:page|category|tag|kategooria)/", urlsplit(u).path)
                ),
                None,
            )
            if post_url:
                post = crawl.get(post_url, quiet=True)
                if _ok(post) and _is_html(post):  # type: ignore[arg-type]
                    ptext, _ = dd.html_to_text_and_links(post["html"], post["url"])  # type: ignore[index]
                    ptop = dd.latest_date(dd.extract_dates(post["html"], ptext, today))  # type: ignore[index]
                    if ptop:
                        page_posts.append((post["url"], ptop[0], ptop[1]))  # type: ignore[index]
                        if page_best is None or ptop[0] > page_best[0]:
                            page_best = (ptop[0], ptop[1], post["url"])  # type: ignore[index]
        else:
            crawl.warn("news page not found")

        # 5. sitemaps: every (loc, lastmod) of the site/post sitemaps, for the publishing cadence
        entries: list[tuple[str, date]] = []
        smhit: dd.SitemapHit | None = None
        sm_used: str | None = None
        for path in SITEMAP_PATHS:
            sitemap_url = origin + path
            sm = crawl.get(sitemap_url, sitemap=True, quiet=True)
            if sm is None:
                break  # sitemap budget spent
            if not _ok(sm):
                continue
            xmls = [(sitemap_url, sm["html"])]
            for child_url in dd.sitemap_children(sm["html"])[:MAX_SITEMAP_CHILDREN]:
                child = crawl.get(child_url, sitemap=True, quiet=True)
                if child is None:
                    break
                if _ok(child):
                    xmls.append((child_url, child["html"]))
            for used, xml in xmls:
                entries += dd.sitemap_entries(xml)
                xml_hit = dd.sitemap_latest(xml, today)
                if xml_hit and (smhit is None or (xml_hit.news, xml_hit.date) > (smhit.news, smhit.date)):
                    smhit, sm_used = xml_hit, used
            break
        if smhit:
            payload["sitemap"] = {
                "url": sm_used,
                "date": smhit.date.isoformat(),
                "loc": smhit.url,
                "news": smhit.news,
            }
        has_sitemap_posts = any(dd.is_news_path(loc) and dd.post_key(loc) for loc, _d in entries)
        posts = dd.post_history(
            entries, page_posts, today, listing_dates=listing_dates, listing_url=news_url or ""
        )
        # Single-date evidence when no post list could be built: a page date, or the newest news-path
        # sitemap entry that is not a post of its own (e.g. the news index).
        single = page_best
        if (
            smhit
            and smhit.news
            and dd.post_key(smhit.url) is None
            and (single is None or smhit.date > single[0])
        ):
            single = (smhit.date, "sitemap_lastmod", smhit.url)
        if posts:
            top_post = posts[0]
            method = "sitemap_lastmod" if top_post.source == "sitemap" else (top_post.method or "page_dates")
            latest: tuple[date, str, str] | None = (top_post.date, method, top_post.url)
        else:
            latest = single
        if latest:
            payload["news"] = {
                "date": latest[0].isoformat(),
                "method": latest[1],
                "url": latest[2],
                "cadence_source": ("sitemap" if has_sitemap_posts else "news_page") if posts else None,
                "posts": [
                    {
                        "key": p.key,
                        "date": p.date.isoformat(),
                        "url": p.url,
                        "source": p.source,
                        "method": p.method,
                    }
                    for p in posts[:MAX_PAYLOAD_POSTS]
                ],
            }

        # 6. careers page
        careers_page = section_pages.get("careers")
        if careers_page:
            curl = careers_page["url"]
            ctext, clinks = dd.html_to_text_and_links(careers_page["html"], curl)
            ev = dd.count_open_roles(ctext, clinks, curl, final_host)
            payload["careers"] = {
                "url": ev.careers_url,
                "open_roles": ev.open_roles,
                "state": ev.state,
                "ats": ev.ats,
                "job_links": [p for p in ev.job_links if "@" not in p][:50],
                "no_openings_phrase": _scrub(ev.no_openings_phrase) if ev.no_openings_phrase else None,
            }
        else:
            crawl.warn("careers page not found")

        return self._snapshot(ref, payload, home_url, home["status"])

    def _snapshot(
        self, ref: CandidateRef, payload: dict[str, Any], home_url: str | None, status: int | None
    ) -> FetchedSnapshot:
        domain = payload.get("domain")
        return FetchedSnapshot(
            ref.reference, payload, home_url or None, status, request_id=f"decay:{domain or ref.reference}"
        )

    # ------------------------------------------------------------------ parse

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        p = snapshot.payload
        careers = p.get("careers")
        hiring = (
            dd.HiringEvidence(careers["state"], careers["open_roles"], careers["url"], careers["ats"])
            if careers
            else dd.HiringEvidence("unknown", None, None, None)
        )
        news = p.get("news")
        posts: list[dd.PostDate] | None = None
        if news and isinstance(news.get("posts"), list):
            posts = [
                dd.PostDate(
                    str(x["key"]),
                    date.fromisoformat(x["date"]),
                    x.get("url") or "",
                    x["source"],
                    x.get("method"),
                )
                for x in news["posts"]
            ]
        copyright = p.get("copyright") or {}
        last_modified = p.get("last_modified") or {}
        sig = dd.score(
            domain=p.get("domain"),
            domain_verification=p.get("domain_verification") or "unverified",
            copyright_year=copyright.get("year"),
            copyright_url=copyright.get("url"),
            news=(date.fromisoformat(news["date"]), news["method"], news["url"]) if news else None,
            hiring=hiring,
            last_modified=last_modified.get("header"),
            last_modified_url=last_modified.get("url"),
            revenue=p.get("revenue"),
            today=date.fromisoformat(p["today"]),
            thresholds=self.thresholds,
            warnings=list(p.get("warnings") or []),
            posts=posts,
            cadence_source=news.get("cadence_source") if news else None,
            headcount=p.get("headcount"),
        )
        home_url = p.get("home_url")
        key = p.get("registry_code") or p.get("company_id")
        verified = sig.domain_verification != "unverified"
        year = sig.copyright["year"]
        news_date = sig.news["latest_date"]
        open_roles = sig.hiring["open_roles"]
        evidence = {
            "website": home_url if verified else None,
            "footer_copyright_year": sig.copyright["evidence_url"],
            "latest_news_date": sig.news["evidence_url"],
            "open_positions": sig.hiring["careers_url"] if open_roles is not None else None,
            "digital_decay_signal": home_url,
        }
        row: dict[str, Any] = {
            "source_key": f"web-decay:{key}",
            "country": "EE",
            "registry_id": p.get("registry_code"),
            "website": sig.domain if verified else None,
            "footer_copyright_year": str(year) if year is not None else None,
            "latest_news_date": news_date,
            "open_positions": str(open_roles) if open_roles is not None else None,
            "digital_decay_signal": json.dumps(sig.to_json(), sort_keys=True, ensure_ascii=False),
            "source_url": home_url,
            "observed_at": None,
            "enrichment_only": True,
            "estimated_fields": list(ESTIMATED_FIELDS),
            "evidence": {k: v for k, v in evidence.items() if v is not None},
            "warnings": sig.warnings,
        }
        verification = sig.domain_verification
        if verification == "registry_email" or p.get("domain_shared"):
            # A mail domain is not a site identity, and a group domain is shared by several register entries.
            row["domain_is_identity"] = False
        if verification != "unverified":
            row["website_verified"] = True
        row["crawl_evidence"] = crawl_evidence(p)
        return [{k: v for k, v in row.items() if v is not None}]


def crawl_evidence(p: dict[str, Any]) -> dict[str, Any]:
    """Evidence trail kept in the snapshot: domains tried, per-page status/errors and verification.
    No page text."""
    return {
        "tried": list(p.get("tried") or []),
        "pages": [
            {
                "url": page.get("url"),
                "status": page.get("status"),
                "error": (str(page["error"])[:EVIDENCE_ERROR_CHARS] if page.get("error") else None),
            }
            for page in p.get("pages") or []
        ],
        "verification_detail": p.get("verification_detail"),
        "domain_source": p.get("domain_source"),
    }
