"""Pure extraction rules for company-website pages (C-tier enrichment). No I/O.

Extracts only what a page explicitly states:
- registry / VAT IDs (accepted only if they pass the national checksum/format validation),
- the legal entity name and managing directors from a DACH Impressum (legally mandated, §5 DDG / §25 MedienG / UWG),
- hiring volume (count of distinct job-posting links on a careers page),
- founder / family-business self-description signals (booleans; no personal names are extracted outside the Impressum).
Nothing is inferred when a page is silent.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.domain import normalize as n

EXTRACTION_VERSION = "web-2026.09.1"

# Page categories and the local-language path/link keywords that identify them.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "impressum": ["impressum", "imprint", "legal-notice", "legal notice", "mentions-legales", "colophon"],
    "about": [
        "ueber-uns",
        "uber-uns",
        "über uns",
        "about",
        "tietoa-meista",
        "tietoa meistä",
        "yritys",
        "om-oss",
        "om oss",
        "om-os",
        "om os",
        "um-okkur",
        "um okkur",
        "unternehmen",
        "company",
        "who-we-are",
    ],
    "team": [
        "team",
        "tiimi",
        "medarbetare",
        "ansatte",
        "medarbejdere",
        "starfsfolk",
        "leadership",
        "geschaeftsleitung",
        "geschäftsleitung",
        "johto",
        "ledelse",
        "ledning",
        "board-of-management",
    ],
    "careers": [
        "karriere",
        "karriär",
        "karriar",
        "career",
        "careers",
        "jobs",
        "job",
        "stellen",
        "ura",
        "rekry",
        "tyopaikat",
        "työpaikat",
        "lediga-tjanster",
        "ledige-stillinger",
        "ledige stillinger",
        "stillinger",
        "laus-storf",
        "laus störf",
        "join-us",
        "work-with-us",
        "vacancies",
    ],
    "contact": ["kontakt", "contact", "yhteystiedot", "hafdu-samband", "hafðu samband"],
}

FALLBACK_PATHS: dict[str, dict[str, list[str]]] = {
    "DE": {"impressum": ["/impressum"], "about": ["/ueber-uns"], "careers": ["/karriere", "/jobs"]},
    "AT": {"impressum": ["/impressum"], "about": ["/ueber-uns"], "careers": ["/karriere", "/jobs"]},
    "CH": {"impressum": ["/impressum"], "about": ["/ueber-uns"], "careers": ["/jobs", "/karriere"]},
    "FI": {"about": ["/tietoa-meista"], "careers": ["/ura", "/rekry"]},
    "SE": {"about": ["/om-oss"], "careers": ["/karriar", "/jobb"]},
    "NO": {"about": ["/om-oss"], "careers": ["/karriere", "/jobb"]},
    "DK": {"about": ["/om-os"], "careers": ["/karriere", "/job"]},
    "IS": {"about": ["/um-okkur"], "careers": ["/laus-storf"]},
}

JOB_LINK_RE = re.compile(
    r"(job|jobs|stelle|stellen|position|vacanc|opening|career[s]?/.+|karriere/.+|ura/.+|rekry/.+|"
    r"lediga|ledige|stilling|stillinger|tyopaikka|työpaikka|avoimet|laus-starf|/apply)",
    re.IGNORECASE,
)
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
)

FOUNDER_WORDS = [
    "founder",
    "co-founder",
    "founded by",
    "gründer",
    "gründerin",
    "mitgründer",
    "firmengründer",
    "perustaja",
    "perustajat",
    "grundare",
    "grundaren",
    "grunnlegger",
    "gründer ",
    "stifter",
    "stiftere",
    "medstifter",
    "stofnandi",
    "stofnendur",
]
FAMILY_WORDS = [
    "family business",
    "family-owned",
    "family owned",
    "familienunternehmen",
    "familiengeführt",
    "familienbetrieb",
    "perheyritys",
    "familjeföretag",
    "familjeägt",
    "familiebedrift",
    "familieeid",
    "familievirksomhed",
    "familieejet",
    "fjölskyldufyrirtæki",
]

LEGAL_FORM_RE = re.compile(
    r"\b(GmbH & Co\.? KGaA|SE & Co\.? KGaA|AG & Co\.? KGaA|GmbH & Co\.? KG|AG & Co\.? KG|SE & Co\.? KG|KGaA|"
    r"GmbH|gGmbH|AG|SE|KG|OHG|UG \(haftungsbeschränkt\)|e\.\s?K\.|e\.U\.|Sàrl|SA|Sagl)(?!\w)"
)
NAME_TOKEN = r"(?:Dr\.\s|Prof\.\s|Dipl\.-\w+\.?\s)?[A-ZÄÖÜ][a-zäöüßéèáàç\-]+(?:\s(?:von|van|de|zu|der)\s?)?"
PERSON_RE = re.compile(rf"({NAME_TOKEN}(?:\s[A-ZÄÖÜ][a-zäöüßéèáàç\-]+){{1,3}})")
MD_LABEL_RE = re.compile(
    r"(Vertretungsberechtigte[r]? Geschäftsführer(?:in)?|Geschäftsführer(?:in|innen)?|Geschäftsführung|"
    r"Geschäftsleitung|vertreten durch(?: den| die)?(?: Geschäftsführer(?:in)?)?|Vorstand|"
    r"Managing Directors?|Board of Management|Management Board|Executive Board|Board of Directors)"
    r"(?![a-zäöü])\s*[:\-–]?\s*",
    re.IGNORECASE,
)
NOT_NAMES = {
    "Geschäftsführer",
    "Geschäftsführung",
    "Registergericht",
    "Amtsgericht",
    "Handelsregister",
    "Registernummer",
    "Umsatzsteuer",
    "Telefon",
    "Kontakt",
    "Sitz",
    "Vorstand",
    "Aufsichtsrat",
    "Inhaltlich",
    "Verantwortlich",
    "Datenschutz",
    "Haftung",
    "Impressum",
    "Email",
    "Mail",
    "Fax",
    "Straße",
    "Strasse",
    "Deutschland",
    "Österreich",
    "Schweiz",
    "Gesellschaft",
    "Firmenbuch",
    "Firmenbuchgericht",
    "Handelsregistereintrag",
    "Geschäftsleitung",
    "Vertreten",
    "Anschrift",
    "Additional",
    "Information",
    "Informationen",
    "Contact",
    "Legal",
    "Notice",
    "Imprint",
    "Privacy",
    "Terms",
    "General",
    "Company",
    "Office",
    "Phone",
    "Responsible",
    "Content",
    "Data",
    "Protection",
    "Group",
    "Board",
    "Management",
    "Directors",
    "Director",
    "Partner",
    "Registered",
    "Court",
    "Register",
    "Supervisory",
    "Chairman",
    "Weitere",
    "Angaben",
    "Hinweis",
    "Hinweise",
    "Rechtliche",
    "Unternehmen",
    "Gruppe",
    "Kontaktieren",
    "Service",
    "Support",
    "Online",
    "Vorständin",
    "Vorsitzender",
    "Vorsitzende",
    "Finanzen",
    "Personal",
    "Technik",
    "Marketing",
    "Vertrieb",
    "Produktion",
    "Sales",
    "Finance",
    "Officer",
    "Chief",
    "Executive",
    "Operating",
    "Technology",
    "Mitglied",
    "Member",
    "Sprecher",
    "Stellvertretender",
    "Stellvertretende",
}


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
    try:
        p.feed(html)
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
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


def categorize_links(links: list[tuple[str, str]], domain: str) -> dict[str, str]:
    """Pick at most one same-site URL per category. A keyword must match a whole path segment/token
    (score 2) or a short link label (score 1); ties go to the shortest, most top-level path."""
    found: dict[str, str] = {}
    for category, words in CATEGORY_KEYWORDS.items():
        best: tuple[int, int, str] | None = None
        for url, label in links:
            if not same_site(url, domain):
                continue
            path = urlsplit(url).path.lower()
            segments = {seg.rsplit(".", 1)[0] for seg in path.split("/") if seg}
            score = 0
            for w in words:
                if " " in w:
                    continue
                if w in segments or ("-" not in w and w in _tokens(path)):
                    score = 2
                    break
            if not score and len(label) <= 40:
                low = label.lower()
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


# ------------------------------------------------------------------ identifiers

_ID_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "NO": [
        re.compile(
            r"(?:org(?:anisasjons)?\.?\s*(?:nr|nummer)\.?|org\.?\s?no\.?|foretaksregisteret)\s*:?\s*(?:NO\s?)?(\d{3}\s?\d{3}\s?\d{3})",
            re.I,
        )
    ],
    "FI": [re.compile(r"(?:y-tunnus|business\s*id|fo-nummer|ly-tunnus)\s*:?\s*(\d{7}-\d)", re.I)],
    "SE": [
        re.compile(
            r"(?:org(?:anisations)?\.?\s*(?:nr|nummer)\.?|corporate identity number)\s*:?\s*(\d{6}-?\d{4})",
            re.I,
        )
    ],
    "DK": [
        re.compile(r"(?:cvr(?:-?nr\.?)?|cvr\s*nummer)\s*:?\s*(?:DK)?\s?(\d{2}\s?\d{2}\s?\d{2}\s?\d{2})", re.I)
    ],
    "IS": [re.compile(r"(?:kennitala|kt\.)\s*:?\s*(\d{6}-?\d{4})", re.I)],
    "CH": [re.compile(r"(CHE[-\s]?\d{3}\.?\d{3}\.?\d{3})", re.I)],
    "AT": [re.compile(r"(?:FN|Firmenbuchnummer|Firmenbuch-Nr\.?)\s*:?\s*(\d{1,6}\s?[a-z])\b", re.I)],
    "DE": [re.compile(r"\b((?:HRB|HRA)\s*\d{1,6}(?:\s?[A-Z]{1,2}\b)?)", re.I)],
}
_VAT_PATTERNS: dict[str, re.Pattern[str]] = {
    "DE": re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3})\b"),
    "AT": re.compile(r"\b(ATU\s?\d{8})\b"),
    "CH": re.compile(r"\b(CHE[-\s]?\d{3}\.?\d{3}\.?\d{3}\s?(?:MWST|TVA|IVA))\b"),
    "FI": re.compile(r"\b(FI\s?\d{8})\b"),
    "SE": re.compile(r"\b(SE\s?\d{12})\b"),
    "DK": re.compile(r"\b(DK\s?\d{8})\b"),
    "NO": re.compile(r"\b(NO\s?\d{9}\s?MVA)\b"),
}
_COURT_BEFORE_RE = re.compile(
    r"(?:Amtsgericht|Registergericht|Reg\.?-?\s?Gericht|Register court|Registration court|Court of registration|"
    r"Registered office|Sitz(?: der Gesellschaft)?|Handelsregister)\s*:?\s*(?:Amtsgericht\s+)?"
    r"([A-ZÄÖÜ][\wäöüß\-]+(?:\s(?:am|an der|im)\s[\wäöüß\-]+)?)[,;]?\s*(?:unter\s)?(?:HRB|HRA)",
    re.IGNORECASE,
)
_COURT_EN_RE = re.compile(  # "Bad Oeynhausen District Court HRA 6218"
    r"([A-ZÄÖÜ][\wäöüß\-]+(?:\s[A-ZÄÖÜ][\wäöüß\-]+)?)\s+(?:District|Local) Court[,:]?\s*(?:HRB|HRA)"
)
_COURT_RE = re.compile(
    r"(?:Amtsgericht|Registergericht\s*:?\s*(?:Amtsgericht)?|Handelsregister(?:gericht)?\s*:?\s*(?:Amtsgericht)?)\s+([A-ZÄÖÜ][\wäöüß\-]+(?:\s(?:am|an der|im|\(\w+\))\s?[\wäöüß\-]+)?)"
)


def extract_registry_ids(text: str, country: str) -> list[str]:
    """Return validated canonical registry keys stated on the page for this country."""
    out: list[str] = []
    for pattern in _ID_PATTERNS.get(country, []):
        for m in pattern.finditer(text):
            raw = m.group(1)
            if country == "DE":
                window = text[max(0, m.start() - 90) : m.end()]
                before = list(_COURT_BEFORE_RE.finditer(window)) or list(_COURT_EN_RE.finditer(window))
                after = _COURT_RE.search(text[m.start() : m.end() + 120])
                if before:
                    raw = f"{raw} {before[-1].group(1)}"
                elif after:
                    raw = f"{raw} {after.group(1)}"
            res = n.normalize_registry_id(country, raw)
            if res.canonical and res.canonical not in out:
                out.append(res.canonical)
    return out


def extract_vat_ids(text: str, country: str) -> list[str]:
    pattern = _VAT_PATTERNS.get(country)
    if not pattern:
        return []
    out = []
    for m in pattern.finditer(text):
        vat, _ = n.normalize_vat(country, m.group(1))
        if vat and vat not in out:
            out.append(vat)
    return out


def extract_legal_name(impressum_text: str) -> str | None:
    """First short line in the Impressum that carries a legal form, e.g. 'Muster Maschinenbau GmbH'."""
    for line in impressum_text.split("\n")[:60]:
        line = line.strip(" ,;:")
        if (
            3 < len(line) <= 90
            and LEGAL_FORM_RE.search(line)
            and not re.search(r"\d{4,}|@|http|©|copyright", line, re.I)
        ):
            m = LEGAL_FORM_RE.search(line)
            assert m is not None
            candidate = line[: m.end()].strip(" ,;:")
            words = candidate.split()
            if 2 <= len(words) <= 9 and candidate[0].isupper():
                return candidate
    return None


def extract_managing_directors(impressum_text: str) -> list[tuple[str, str]]:
    """(name, role) pairs stated after an explicit label. Returns [] when unclear (never guesses)."""
    people: list[tuple[str, str]] = []
    for m in MD_LABEL_RE.finditer(impressum_text):
        label = m.group(1).strip()
        low = label.lower()
        if low.startswith(("vorstand", "board of management", "management board", "executive board")):
            role = "Vorstand / Executive Board"
        elif low.startswith("board of directors"):
            role = "Board of Directors"
        else:
            role = "Geschäftsführer / Managing Director"
        window = impressum_text[m.end() : m.end() + 300]
        segment = re.split(
            r"\n(?=[A-ZÄÖÜ][\w \-]{2,40}:)|Registergericht|Amtsgericht|Handelsregister|Registered|Sitz|Telefon|"
            r"Phone|E-Mail|e-mail|USt|VAT|Copyright|©|Chairman|Vorsitz|Aufsichtsrat|Supervisory",
            window,
        )[0]
        segment = re.sub(r"\([^)]*\)", "", segment)  # drop "(Chief Executive Officer)" style qualifiers
        parts = re.split(r",|;|\bund\b|\band\b|&|\n|/", segment)
        if segment == window and len(window) == 300:
            parts = parts[:-1]  # the window cut the text mid-name: never keep a truncated name
        for part in parts:
            part = part.strip(" .:-–")
            pm = PERSON_RE.fullmatch(part)
            if not pm:
                continue
            name = pm.group(1).strip()
            tokens = name.replace("Dr. ", "").replace("Prof. ", "").split()
            if len(tokens) < 2 or any(t in NOT_NAMES for t in tokens) or LEGAL_FORM_RE.search(name):
                continue
            if name not in [p[0] for p in people]:
                people.append((name, role))
        if len(people) >= 6:
            break
    return people


def count_job_postings(
    links: list[tuple[str, str]], careers_url: str, domain: str
) -> tuple[int | None, str | None]:
    """Distinct job-posting links on the careers page. Returns (None, ats_host) when postings are hosted
    on an external applicant-tracking system we do not crawl, and (None, None) when nothing is detectable."""
    careers_path = urlsplit(careers_url).path.rstrip("/")
    postings: set[str] = set()
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
        if JOB_LINK_RE.search(path) and len(label) >= 4:
            postings.add(path)
    if postings:
        return len(postings), ats
    return None, ats


def has_any(text: str, words: list[str]) -> bool:
    low = text.lower()
    return any(w in low for w in words)
