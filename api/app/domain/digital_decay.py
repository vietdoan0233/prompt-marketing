"""Digital-decay signals for a company's own website. Pure rules: no I/O, no clock, no LLM.

Three checks, each explicitly `unknown` when a page is missing or silent (never scored as stale):
- copyright: the newest year in the footer copyright notice,
- news: publishing cadence of news/press/blog posts: the newest post and the number of distinct posts in
  the stale window (sitemap lastmod per post, translations counted once, bulk re-save dates ignored,
  on-page publication dates beating lastmod; the news listing page's dates when there is no post sitemap),
- hiring: open roles on the careers page (`zero_roles` only when the page exists and shows none).
Register headcount (FTE trend from filed annual reports) is passed through as context for the `watch`
verdict; it is official data, not a website check, and never counts towards the stale/determinable counts.
The verdict is deterministic for the same evidence, `today` and thresholds.
"""

import contextlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from app.domain.normalize import normalize_name, strip_accents

DECAY_VERSION = "decay-2026.09.3"


@dataclass(frozen=True)
class Thresholds:
    copyright_stale_years: int = 2
    news_stale_months: int = 18
    min_revenue_eur: int = 5_000_000
    news_min_posts: int = 3
    headcount_flat_pct: int = 10


# ------------------------------------------------------------------ HTML -> text and links


class _TextAndLinks(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}
    BLOCK = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "footer",
        "header",
        "address",
        "td",
        "dd",
        "dt",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._anchor).split())))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data)


def html_to_text_and_links(html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    p = _TextAndLinks()
    with contextlib.suppress(Exception):  # malformed HTML: keep whatever was parsed
        p.feed(html)
    raw = re.sub("[­​‌‍﻿]", "", "".join(p.parts))  # soft hyphens / zero-width chars
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    text = "\n".join(line for line in lines if line)
    links = []
    for href, label in p.links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append((urljoin(base_url, href).split("#")[0], label))
    return text, links


def same_site(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[/\-_.\s?=&]+", text.lower()) if t}


def _fold(text: str) -> str:
    """Accent-free lowercase comparison key ("Karjäär" -> "karjaar")."""
    return strip_accents(text).lower()


_FOOTER_RE = re.compile(r"<footer\b.*?</footer>", re.S | re.I)


def footer_text(html: str) -> str:
    """Visible text of the last <footer>; without one, the last 20% of the page's visible text."""
    matches = _FOOTER_RE.findall(html)
    if matches:
        return html_to_text_and_links(matches[-1], "")[0]
    text = html_to_text_and_links(html, "")[0]
    return text[int(len(text) * 0.8) :]


# ------------------------------------------------------------------ domain candidates

EXTRA_LEGAL_WORDS = {
    "osauhing",
    "aktsiaselts",
    "usaldusuhing",
    "taisuhing",
    "tulundusuhistu",
    "euroopa",
    "filiaal",
    "eesti",
}  # compared after normalize_name (accent-free, lowercase)
CANDIDATE_TLDS = (".ee", ".com", ".eu")
MAX_DOMAIN_CANDIDATES = 8
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def domain_candidates(legal_name: str) -> list[str]:
    """Guessed hostnames for a legal name, most likely first. Guesses are only ever probes: a candidate
    is accepted by the connector only after verify_identity finds the company on the site itself."""
    words = normalize_name(legal_name).split()
    kept = [w for w in words if w not in EXTRA_LEGAL_WORDS or w == "eesti"]
    if "eesti" in kept and len(kept) > 1:
        kept = [w for w in kept if w != "eesti"]
    words = kept
    if not words:
        return []
    stems: list[str] = ["".join(words)]
    if len(words) > 1:
        stems.append("-".join(words))
        if len(words[0]) >= 4:
            stems.append(words[0])
    unique_stems = list(dict.fromkeys(stems))
    out = [stem + tld for stem in unique_stems for tld in CANDIDATE_TLDS if _LABEL_RE.match(stem)]
    return out[:MAX_DOMAIN_CANDIDATES]


# ------------------------------------------------------------------ identity verification

REG_CODE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_HOUSE_NO_RE = re.compile(r"^\d+[a-z]?$")


def verify_identity(
    text: str,
    *,
    registry_code: str | None,
    legal_name: str,
    postal_code: str | None,
    street: str | None,
) -> str:
    """ "registry_code" | "name_and_address" | "unverified". A name alone never verifies a domain."""
    if registry_code and re.fullmatch(r"\d{8}", registry_code):
        compact = re.sub(r"(?<=\d)\s(?=\d)", "", text)
        for candidate in (text, compact):
            if registry_code in REG_CODE_RE.findall(candidate):
                return "registry_code"
    name_key = normalize_name(legal_name)
    if name_key and name_key in normalize_name(text):
        postal = (postal_code or "").strip()
        if re.fullmatch(r"\d{5}", postal) and re.search(rf"(?<!\d){postal}(?!\d)", text):
            return "name_and_address"
        if street:
            tokens = re.findall(r"[a-z0-9]+", _fold(street))
            house = next((t for t in tokens if _HOUSE_NO_RE.match(t)), None)
            first = tokens[0] if tokens else ""
            if len(first) >= 4 and not first.isdigit() and house:
                folded = _fold(text)
                pattern = rf"\b{re.escape(first)}\b[^\n]{{0,40}}?(?<![0-9a-z]){re.escape(house)}(?![0-9a-z])"
                if re.search(pattern, folded):
                    return "name_and_address"
    return "unverified"


# ------------------------------------------------------------------ copyright year

COPYRIGHT_RE = re.compile(
    r"(?:©|&copy;|\(c\)|copyright|autoriõigus(?:ed)?|kõik õigused kaitstud|all rights reserved)"
    r"[^\n]{0,80}?((?:19|20)\d{2})(?:\s*[-–—/]\s*((?:19|20)\d{2}))?",
    re.I,
)
COPYRIGHT_REVERSED_RE = re.compile(r"((?:19|20)\d{2})\s*(?:©|&copy;|\(c\))", re.I)


def copyright_match(footer: str, full_text: str, today: date) -> tuple[int, str] | None:
    """(newest plausible year, matched notice text). The footer is searched first; the full text only
    when the footer yields nothing."""
    for source in (footer, full_text):
        found: list[tuple[int, str]] = []
        for m in COPYRIGHT_RE.finditer(source):
            year = max(int(m.group(1)), int(m.group(2) or m.group(1)))
            found.append((year, m.group(0)))
        for m in COPYRIGHT_REVERSED_RE.finditer(source):
            found.append((int(m.group(1)), m.group(0)))
        valid = [(y, s) for y, s in found if 1995 <= y <= today.year]
        if valid:
            return max(valid, key=lambda pair: pair[0])
        if found:
            return None  # matches exist but none plausible: do not fall through to the full text
    return None


def extract_copyright_year(footer: str, full_text: str, today: date) -> int | None:
    hit = copyright_match(footer, full_text, today)
    return hit[0] if hit else None


# ------------------------------------------------------------------ dates (news recency)

ET_MONTHS = {
    "jaanuar": 1,
    "veebruar": 2,
    "märts": 3,
    "marts": 3,
    "aprill": 4,
    "mai": 5,
    "juuni": 6,
    "juuli": 7,
    "august": 8,
    "september": 9,
    "oktoober": 10,
    "november": 11,
    "detsember": 12,
}
EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTHS = {**EN_MONTHS, **ET_MONTHS}
_MONTH_ALT = "|".join(re.escape(m) for m in sorted(_MONTHS, key=len, reverse=True))

_TIME_TAG_RE = re.compile(r"<time[^>]*datetime=[\"']([^\"']+)[\"']", re.I)
_JSONLD_RE = re.compile(r"\"date(?:Published|Modified|Created)\"\s*:\s*\"([^\"]+)\"")
_META_RE = re.compile(
    r"<meta[^>]+(?:article:published_time|article:modified_time|og:updated_time)[^>]+content=[\"']([^\"']+)[\"']",
    re.I,
)
_ISO_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")
_D_MONTH_Y_RE = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALT})\.?\s+(20\d{{2}})\b", re.I)
_MONTH_D_Y_RE = re.compile(rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I)

DATE_METHODS = ("time_tag", "jsonld", "meta", "page_dates")  # priority order on equal dates
_MIN_DATE = date(2000, 1, 1)


def _iso_prefix(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _ymd(y: str | int, m: str | int, d: str | int) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def extract_dates(html: str, text: str, today: date) -> list[tuple[date, str]]:
    """Every plausible (date, method) on a page. Invalid and future dates are dropped."""
    raw: list[tuple[date | None, str]] = []
    raw += [(_iso_prefix(v), "time_tag") for v in _TIME_TAG_RE.findall(html)]
    raw += [(_iso_prefix(v), "jsonld") for v in _JSONLD_RE.findall(html)]
    raw += [(_iso_prefix(v), "meta") for v in _META_RE.findall(html)]
    raw += [(_ymd(y, m, d), "page_dates") for y, m, d in _ISO_RE.findall(text)]
    raw += [(_ymd(y, m, d), "page_dates") for d, m, y in _DMY_RE.findall(text)]
    raw += [(_ymd(y, _MONTHS[mon.lower()], d), "page_dates") for d, mon, y in _D_MONTH_Y_RE.findall(text)]
    raw += [(_ymd(y, _MONTHS[mon.lower()], d), "page_dates") for mon, d, y in _MONTH_D_Y_RE.findall(text)]
    return [(d, method) for d, method in raw if d is not None and _MIN_DATE <= d <= today]


def latest_date(found: list[tuple[date, str]]) -> tuple[date, str] | None:
    """Newest date; on equal dates the more structured method wins (DATE_METHODS order)."""
    if not found:
        return None

    def rank(pair: tuple[date, str]) -> tuple[date, int]:
        method = pair[1]
        prio = DATE_METHODS.index(method) if method in DATE_METHODS else len(DATE_METHODS)
        return (pair[0], -prio)

    return max(found, key=rank)


# ------------------------------------------------------------------ section links

NEWS_KEYWORDS = [
    "uudised",
    "uudis",
    "blogi",
    "blog",
    "pressiteated",
    "pressiteade",
    "meedia",
    "press",
    "news",
    "newsroom",
    "artiklid",
    "articles",
    "insights",
    "новости",
    "media",
]
CAREERS_KEYWORDS = [
    "karjaar",
    "karjäär",
    "tööpakkumised",
    "toopakkumised",
    "tööpakkumine",
    "vabad-ametikohad",
    "vabad ametikohad",
    "töökohad",
    "tookohad",
    "tule-meile-toole",
    "tule meile tööle",
    "careers",
    "career",
    "jobs",
    "join-us",
    "work-with-us",
    "vacancies",
    "вакансии",
    "karriere",
    "rekry",
]
CONTACT_KEYWORDS = ["kontakt", "contact", "meist", "about", "ettevottest", "ettevõttest"]
SECTION_KEYWORDS: dict[str, list[str]] = {"news": NEWS_KEYWORDS, "careers": CAREERS_KEYWORDS}
FALLBACK_PATHS = {
    "news": ["/uudised", "/blogi", "/news", "/blog"],
    "careers": ["/karjaar", "/toopakkumised", "/careers", "/jobs"],
}


def find_section_links(
    links: list[tuple[str, str]], domain: str, categories: dict[str, list[str]] | None = None
) -> dict[str, str]:
    """At most one same-site URL per category. A keyword must match a whole path segment/token
    (score 2) or a short link label (score 1); ties go to the shortest, most top-level path.
    Paths, labels and keywords are compared accent-free, so "Karjäär" matches "karjaar"."""
    found: dict[str, str] = {}
    for category, raw_words in (categories or SECTION_KEYWORDS).items():
        words = list(dict.fromkeys(_fold(w) for w in raw_words))
        best: tuple[int, int, str] | None = None
        for url, label in links:
            if not same_site(url, domain):
                continue
            path = _fold(unquote(urlsplit(url).path))
            segments = {seg.rsplit(".", 1)[0] for seg in path.split("/") if seg}
            score = 0
            for w in words:
                if " " in w:
                    continue
                if w in segments or ("-" not in w and w in _tokens(path)):
                    score = 2
                    break
            if not score and len(label) <= 40:
                low = _fold(label)
                if any(w == low or (" " in w and w in low) or w in _tokens(low) for w in words):
                    score = 1
            if not score:
                continue
            cand = (-score, len(path), url)
            if best is None or cand < best:
                best = cand
        if best:
            found[category] = best[2]
    return found


# ------------------------------------------------------------------ sitemap


@dataclass(frozen=True)
class SitemapHit:
    date: date
    url: str  # the <loc> of the newest entry
    news: bool  # True only when the entry's path is a news/press path


LASTMOD_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>(?:(?!</url>).)*?<lastmod>([^<]+)</lastmod>", re.S | re.I)
_SITEMAP_CHILD_RE = re.compile(r"<sitemap>\s*<loc>([^<]+)</loc>", re.S | re.I)


def _is_news_path(url: str) -> bool:
    path = _fold(unquote(urlsplit(url.strip()).path))
    tokens = _tokens(path) | {seg for seg in path.split("/") if seg}
    return any(_fold(w) in tokens for w in NEWS_KEYWORDS)


def is_news_path(url: str) -> bool:
    """True when a URL's path has a news/press/blog segment or token."""
    return _is_news_path(url)


def sitemap_latest(xml: str, today: date) -> SitemapHit | None:
    """Newest lastmod, preferring news/press entries. A whole-site lastmod (news=False) is often
    regenerated by the CMS and must not count as news recency."""
    entries: list[tuple[date, str]] = []
    for loc, lastmod in LASTMOD_RE.findall(xml):
        d = _iso_prefix(lastmod)
        if d is not None and _MIN_DATE <= d <= today:
            entries.append((d, loc.strip()))
    if not entries:
        return None
    news = [e for e in entries if _is_news_path(e[1])]
    pool = news or entries
    d, loc = max(pool, key=lambda e: (e[0], e[1]))
    return SitemapHit(d, loc, bool(news))


def sitemap_entries(xml: str) -> list[tuple[str, date]]:
    """Every (loc, lastmod) pair of a urlset sitemap; entries without a parseable lastmod are skipped."""
    out: list[tuple[str, date]] = []
    for loc, lastmod in LASTMOD_RE.findall(xml):
        d = _iso_prefix(lastmod)
        if d is not None:
            out.append((loc.strip(), d))
    return out


LANG_SEGMENTS = {"et", "en", "ru", "fi", "lv", "lt", "de", "sv", "no", "da", "pl", "uk"}
BULK_DATE_MIN_POSTS = (
    4  # >= this many distinct posts sharing one lastmod date = a bulk re-save, not publishing
)
_NEWS_INDEX_SEGMENTS = {_fold(w) for w in NEWS_KEYWORDS}
_LISTING_SEGMENTS = {"page", "category", "tag", "kategooria", "author"}


def post_key(url: str) -> str | None:
    """Identity of a post across translations: its slug (last path segment, lowercased, URL-decoded), with
    a leading language segment ignored. None for the root, a news section index or a listing/archive page."""
    segments = [_fold(unquote(seg)) for seg in urlsplit(url.strip()).path.split("/") if seg]
    if segments and segments[0] in LANG_SEGMENTS:
        segments = segments[1:]
    if not segments:
        return None
    last = segments[-1]
    if last.rsplit(".", 1)[0] in _NEWS_INDEX_SEGMENTS:
        return None
    if any(seg in _LISTING_SEGMENTS for seg in segments[:-1]):
        return None  # /blog/page/2/, /blog/category/x/, /blog/tag/y/
    return last


@dataclass(frozen=True)
class PostDate:
    key: str
    date: date
    url: str
    source: str  # "page" | "sitemap"
    method: str | None = None  # page date method (time_tag/jsonld/meta/page_dates); None for sitemap


def post_history(
    sitemap_entries: list[tuple[str, date]],
    page_posts: list[tuple[str, date, str]],
    today: date,
    listing_dates: list[tuple[date, str]] | None = None,
    listing_url: str = "",
) -> list[PostDate]:
    """One dated entry per distinct post, newest first.

    sitemap_entries: (loc, lastmod) from post/news sitemaps; only news-path locs with a post key count, and a
    post's date is the EARLIEST lastmod across its translations. A lastmod shared by >= BULK_DATE_MIN_POSTS
    posts is a bulk re-save and is dropped. page_posts: (post url, on-page date, method); an on-page date
    beats the sitemap lastmod of the same post. listing_dates: the news listing page's dates, used only
    when the sitemaps hold no post entries at all (each distinct date = one post)."""

    def valid(d: date) -> bool:
        return _MIN_DATE <= d <= today

    earliest: dict[str, tuple[date, str]] = {}
    for loc, d in sitemap_entries:
        if not valid(d) or not _is_news_path(loc):
            continue
        key = post_key(loc)
        if key is None:
            continue
        if key not in earliest or (d, loc) < earliest[key]:
            earliest[key] = (d, loc)
    per_date = Counter(d for d, _ in earliest.values())
    bulk = {d for d, count in per_date.items() if count >= BULK_DATE_MIN_POSTS}
    posts: dict[str, PostDate] = {
        key: PostDate(key, d, loc, "sitemap") for key, (d, loc) in earliest.items() if d not in bulk
    }
    page_dates: set[date] = set()
    for url, d, method in page_posts:
        if not valid(d):
            continue
        key = post_key(url) or f"page:{url}"
        current = posts.get(key)
        if current is None or current.source == "sitemap" or d > current.date:
            posts[key] = PostDate(key, d, url, "page", method)
        page_dates.add(d)
    if not earliest and listing_dates:
        best: dict[date, str] = {}
        for d, method in listing_dates:
            if valid(d) and d not in page_dates:
                prev = best.get(d)
                if prev is None or _method_rank(method) < _method_rank(prev):
                    best[d] = method
        for d, method in best.items():
            key = f"listing:{d.isoformat()}"
            posts[key] = PostDate(key, d, listing_url, "page", method)
    return sorted(posts.values(), key=lambda p: (p.date, p.key), reverse=True)


def _method_rank(method: str) -> int:
    return DATE_METHODS.index(method) if method in DATE_METHODS else len(DATE_METHODS)


def sitemap_children(xml: str) -> list[str]:
    """Child sitemap URLs of a sitemap index, news-like ones first."""
    children = [c.strip() for c in _SITEMAP_CHILD_RE.findall(xml)]
    preferred = [c for c in children if any(k in c.lower() for k in ("post", "news", "uudis", "blog"))]
    return preferred + [c for c in children if c not in preferred]


# ------------------------------------------------------------------ open roles

ATS_HOSTS = (
    "personio",
    "greenhouse.io",
    "lever.co",
    "workable.com",
    "teamtailor.com",
    "recruitee.com",
    "jobylon.com",
    "softgarden",
    "smartrecruiters.com",
    "join.com",
    "breezy.hr",
    "varbi.com",
    "jobteaser",
    "laura.fi",
    "reachmee",
    "webcruiter",
    "hrmanager.no",
    "emply.com",
    "hr-manager.net",
    "cv.ee",
    "cvkeskus.ee",
    "cvonline",
    "workday",
    "bamboohr",
    "jobs.lever.co",
)
JOB_LINK_RE = re.compile(
    r"(job|jobs|position|vacanc|opening|career[s]?/.+|karjaar/.+|toopakkumi|"
    r"toopakkumised/.+|ametikoht|/apply|/kandideeri)",
    re.I,
)
_NO_OPENINGS_PATTERN = (
    r"(hetkel\s+(?:(?:avatud|vabu|vabad)\s+)?(?:ametikohti|töökohti|tööpakkumisi|positsioone)\s+(?:ei\s+ole|pole)|"
    r"(?:avatud|vabu)\s+(?:ametikohti|töökohti)\s+(?:hetkel\s+)?(?:ei\s+ole|pole)|"
    r"no\s+(?:current\s+)?(?:open\s+)?(?:positions|vacancies|openings|job\s+openings)|"
    r"there\s+are\s+(?:currently\s+)?no\s+(?:open\s+)?(?:positions|vacancies|openings)|"
    r"we\s+are\s+not\s+(?:currently\s+)?hiring)"
)
NO_OPENINGS_RE = re.compile(_NO_OPENINGS_PATTERN, re.I)
_NO_OPENINGS_FOLDED_RE = re.compile(strip_accents(_NO_OPENINGS_PATTERN), re.I)  # "tookohti" etc.
_CAREER_INDEX_SEGMENTS = {_fold(w) for w in CAREERS_KEYWORDS if " " not in w}


@dataclass
class HiringEvidence:
    state: str  # "hiring" | "zero_roles" | "unknown"
    open_roles: int | None
    careers_url: str | None
    ats: str | None
    job_links: list[str] = field(default_factory=list)  # distinct posting paths (evidence only)
    no_openings_phrase: str | None = None  # the matched "no open positions" phrase, <=80 chars


def _job_postings(
    links: list[tuple[str, str]], careers_url: str, domain: str
) -> tuple[list[str], str | None]:
    """Distinct same-site job-posting paths below the careers page, plus the first external ATS host."""
    careers_path = urlsplit(careers_url).path.rstrip("/")
    postings: list[str] = []
    ats: str | None = None
    for url, label in links:
        host = (urlsplit(url).hostname or "").lower()
        if any(a in host for a in ATS_HOSTS):
            ats = ats or host
            continue
        if not same_site(url, domain):
            continue
        path = urlsplit(url).path.rstrip("/")
        if path == careers_path or path.count("/") < careers_path.count("/"):
            continue
        last = _fold(unquote(path.rsplit("/", 1)[-1]))
        if last in _CAREER_INDEX_SEGMENTS:  # nav link to a careers index, not a posting
            continue
        folded = _fold(unquote(path))
        is_job = JOB_LINK_RE.search(path) or JOB_LINK_RE.search(folded)
        if is_job and len(label) >= 4 and path not in postings:
            postings.append(path)
    return postings, ats


def count_open_roles(
    careers_text: str | None,
    careers_links: list[tuple[str, str]],
    careers_url: str | None,
    domain: str,
) -> HiringEvidence:
    """Open roles on a fetched careers page. Without a careers page the state is unknown, never zero."""
    if careers_url is None:
        return HiringEvidence("unknown", None, None, None)
    postings, ats = _job_postings(careers_links, careers_url, domain)
    if postings:
        return HiringEvidence("hiring", len(postings), careers_url, ats, job_links=postings[:50])
    text = careers_text or ""
    m = NO_OPENINGS_RE.search(text) or _NO_OPENINGS_FOLDED_RE.search(strip_accents(text))
    if m:
        return HiringEvidence("zero_roles", 0, careers_url, ats, no_openings_phrase=m.group(0)[:80])
    if ats:
        return HiringEvidence("unknown", None, careers_url, ats)  # postings live on an ATS we don't crawl
    return HiringEvidence("zero_roles", 0, careers_url, None)


# ------------------------------------------------------------------ scoring


@dataclass
class DigitalDecaySignal:
    domain: str | None
    domain_verification: str
    copyright: dict[str, Any]
    news: dict[str, Any]
    hiring: dict[str, Any]
    last_modified: dict[str, Any]
    stale_count: int
    determinable_count: int
    revenue: dict[str, Any] | None
    verdict: str
    warnings: list[str]
    headcount: dict[str, Any] | None = None
    version: str = DECAY_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "domain": self.domain,
            "domain_verification": self.domain_verification,
            "checks": {
                "copyright": self.copyright,
                "news": self.news,
                "hiring": self.hiring,
                "last_modified": self.last_modified,
                "headcount": self.headcount,
            },
            "stale_count": self.stale_count,
            "determinable_count": self.determinable_count,
            "revenue": self.revenue,
            "verdict": self.verdict,
            "warnings": self.warnings,
        }


def months_between(earlier: date, later: date) -> int:
    return (
        (later.year - earlier.year) * 12
        + (later.month - earlier.month)
        - (1 if later.day < earlier.day else 0)
    )


def score(
    *,
    domain: str | None,
    domain_verification: str,
    copyright_year: int | None,
    copyright_url: str | None,
    news: tuple[date, str, str] | None,  # (date, method, url): single-date evidence
    hiring: HiringEvidence,
    last_modified: str | None,
    last_modified_url: str | None,
    revenue: dict[str, Any] | None,
    today: date,
    thresholds: Thresholds,
    warnings: list[str] | None = None,
    posts: list[PostDate] | None = None,
    cadence_source: str | None = None,
    headcount: dict[str, Any] | None = None,
) -> DigitalDecaySignal:
    notes = list(warnings or [])

    copyright: dict[str, Any]
    if copyright_year is None:
        copyright = {"state": "unknown", "year": None, "age_years": None, "evidence_url": None}
    else:
        age = today.year - copyright_year
        copyright = {
            "state": "stale" if age >= thresholds.copyright_stale_years else "fresh",
            "year": copyright_year,
            "age_years": age,
            "evidence_url": copyright_url,
        }

    ordered = sorted(posts or [], key=lambda p: (p.date, p.key), reverse=True)
    latest: tuple[date, str, str] | None = None
    posts_window: int | None = None
    if ordered:
        top = ordered[0]
        method = "sitemap_lastmod" if top.source == "sitemap" else (top.method or "page_dates")
        latest = (top.date, method, top.url)
        posts_window = sum(1 for p in ordered if months_between(p.date, today) < thresholds.news_stale_months)
    elif news is not None:
        latest = news
    if latest is None:
        news_check: dict[str, Any] = {
            "state": "unknown",
            "latest_date": None,
            "age_months": None,
            "evidence_url": None,
            "method": None,
            "posts_18m": None,
            "post_dates": [],
            "cadence_source": None,
            "reason": None,
        }
        notes.append("no dated news/press found")
    else:
        d, method, url = latest
        m = months_between(d, today)
        reason: str | None = None
        if m >= thresholds.news_stale_months:
            reason = "no_recent_post"
        elif posts_window is not None and posts_window < thresholds.news_min_posts:
            reason = "low_cadence"
        news_check = {
            "state": "stale" if reason else "fresh",
            "latest_date": d.isoformat(),
            "age_months": m,
            "evidence_url": url or None,
            "method": method,
            "posts_18m": posts_window,
            "post_dates": [p.date.isoformat() for p in ordered[:10]],
            "cadence_source": (
                cadence_source or ("sitemap" if ordered[0].source == "sitemap" else "news_page")
            )
            if ordered
            else None,
            "reason": reason,
        }

    hiring_check = {
        "state": hiring.state,
        "open_roles": hiring.open_roles,
        "careers_url": hiring.careers_url,
        "ats": hiring.ats,
    }
    last_mod = {"header": last_modified, "url": last_modified_url}

    states = (copyright["state"], news_check["state"], hiring.state)
    determinable = sum(1 for s in states if s != "unknown")
    stale = (
        (copyright["state"] == "stale") + (news_check["state"] == "stale") + (hiring.state == "zero_roles")
    )

    revenue_ok = (
        isinstance(revenue, dict)
        and revenue.get("currency") == "EUR"
        and isinstance(revenue.get("amount"), int | float)
        and revenue["amount"] >= thresholds.min_revenue_eur
    )
    if domain_verification == "unverified" or determinable < 2:
        verdict = "insufficient_evidence"
    elif stale >= 2 and hiring.state == "zero_roles" and revenue_ok:
        verdict = "coasting"
    elif stale >= 2:
        verdict = "decaying"
        if hiring.state == "zero_roles":
            notes.append("revenue below threshold or unknown")
    elif (
        revenue_ok
        and hiring.state == "zero_roles"
        and isinstance(headcount, dict)
        and headcount.get("state") in {"flat", "shrinking"}
    ):
        verdict = "watch"
        notes.append(
            "zero open roles and flat/shrinking register headcount at "
            f"≥€{thresholds.min_revenue_eur / 1e6:g}M revenue; website otherwise maintained"
        )
    else:
        verdict = "active"

    return DigitalDecaySignal(
        domain=domain,
        domain_verification=domain_verification,
        copyright=copyright,
        news=news_check,
        hiring=hiring_check,
        last_modified=last_mod,
        stale_count=int(stale),
        determinable_count=determinable,
        revenue=revenue,
        verdict=verdict,
        warnings=list(dict.fromkeys(notes)),
        headcount=headcount,
    )
