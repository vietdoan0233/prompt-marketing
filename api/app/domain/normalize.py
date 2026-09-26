"""Deterministic, versioned normalization rules. Pure functions: no I/O, no framework imports.

Bump NORMALIZATION_VERSION whenever an output of this module changes for the same input.
"""

import math
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

NORMALIZATION_VERSION = "norm-2026.09.1"

NORDICS = ("FI", "SE", "NO", "DK", "IS")
DACH = ("DE", "AT", "CH")
BALTICS = ("EE",)

_COUNTRY_ALIASES = {
    "FI": ["fi", "fin", "finland", "suomi"],
    "SE": ["se", "swe", "sweden", "sverige"],
    "NO": ["no", "nor", "norway", "norge", "noreg"],
    "DK": ["dk", "dnk", "denmark", "danmark"],
    "IS": ["is", "isl", "iceland", "island"],
    "DE": ["de", "deu", "germany", "deutschland"],
    "AT": ["at", "aut", "austria", "osterreich", "oesterreich"],
    "CH": ["ch", "che", "switzerland", "schweiz", "suisse", "svizzera"],
    "EE": ["ee", "est", "estonia", "eesti"],
}
_COUNTRY_LOOKUP = {alias: code for code, aliases in _COUNTRY_ALIASES.items() for alias in aliases}


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    key = strip_accents(value).strip().lower()
    return _COUNTRY_LOOKUP.get(key)


def region_for_country(country: str | None) -> str | None:
    if country in NORDICS:
        return "nordics"
    if country in DACH:
        return "dach"
    if country in BALTICS:
        return "baltics"
    return None


# --------------------------------------------------------------------------- names

_LEGAL_FORMS = [
    "gmbh & co. kg",
    "gmbh & co kg",
    "oyj",
    "oy",
    "ab (publ)",
    "publ",
    "ab",
    "asa",
    "as",
    "a/s",
    "aps",
    "i/s",
    "k/s",
    "hf",
    "ehf",
    "gmbh",
    "ag",
    "kg",
    "ohg",
    "ug (haftungsbeschrankt)",
    "ug",
    "e.k.",
    "ek",
    "sarl",
    "sa",
    "sagl",
    "ltd",
    "limited",
    "se",
    "ky",
    "tmi",
    "hb",
    "kb",
    "ans",
    "da",
    "enk",
    # Estonia (accents are stripped before matching: OÜ -> ou, TÜH -> tuh)
    "ou",
    "tuh",
    "uu",
    "tu",
    "mtu",
]
_LEGAL_FORM_RE = re.compile(
    r"(?:^|\s)(?:"
    + "|".join(re.escape(f) for f in sorted(_LEGAL_FORMS, key=len, reverse=True))
    + r")(?=\s|$)"
)


def normalize_name(value: str) -> str:
    """Comparison key for names: casefolded, accent-free, legal-form-free. Never displayed."""
    text = strip_accents(value).casefold()
    text = text.replace("&", " & ")
    text = re.sub(r"[,\.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _LEGAL_FORM_RE.sub(" ", " " + text + " ")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- domains

# Shared hosts are never identity keys: two companies on gmail.com are not the same company.
GENERIC_DOMAINS = {
    "gmail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "gmx.de",
    "gmx.at",
    "web.de",
    "t-online.de",
    "bluewin.ch",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "wixsite.com",
    "business.site",
    "google.com",
    "sites.google.com",
    # Free-mail / consumer ISP mailbox hosts (Estonia, Baltics, international)
    "hot.ee",
    "mail.ee",
    "neti.ee",
    "online.ee",
    "zone.ee",
    "elion.ee",
    "starman.ee",
    "uninet.ee",
    "suurlinn.ee",
    "hotmail.ee",
    "inbox.lv",
    "inbox.lt",
    "mail.ru",
    "yandex.ru",
    "yandex.com",
    "live.com",
    "msn.com",
    "me.com",
    "protonmail.com",
    "proton.me",
    "gmx.com",
    "gmx.net",
    "aol.com",
    "outlook.ee",
    "windowslive.com",
}


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().lower()
    if "@" in raw and "://" not in raw:
        raw = raw.split("@", 1)[1]
    if "://" not in raw:
        raw = "http://" + raw
    host = (urlsplit(raw).hostname or "").strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    return host


def is_generic_domain(domain: str) -> bool:
    return domain in GENERIC_DOMAINS or any(domain.endswith("." + g) for g in GENERIC_DOMAINS)


def normalize_website(value: str | None) -> str | None:
    domain = normalize_domain(value)
    return f"https://{domain}" if domain else None


# --------------------------------------------------------------------------- registry & VAT IDs


@dataclass(frozen=True)
class RegistryIdResult:
    canonical: str | None  # "FI:1234567-8"
    derived_vat: str | None  # VAT key derived deterministically from the registry ID, if the country allows
    warning: str | None


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _fi_check(d7: str) -> int | None:
    rem = sum(int(a) * w for a, w in zip(d7, (7, 9, 10, 5, 8, 4, 2), strict=True)) % 11
    if rem == 1:
        return None
    return 0 if rem == 0 else 11 - rem


def _ee_check(d7: str) -> int:
    for weights in ((1, 2, 3, 4, 5, 6, 7), (3, 4, 5, 6, 7, 8, 9)):
        rem = sum(int(a) * w for a, w in zip(d7, weights, strict=True)) % 11
        if rem != 10:
            return rem
    return 0


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mod11_check(digits: str, weights: tuple[int, ...]) -> int | None:
    rem = sum(int(a) * w for a, w in zip(digits, weights, strict=True)) % 11
    check = 11 - rem
    if check == 11:
        return 0
    if check == 10:
        return None
    return check


def normalize_registry_id(country: str, value: str | None) -> RegistryIdResult:
    if not value or not value.strip():
        return RegistryIdResult(None, None, None)
    raw = value.strip()
    d = _digits(raw)

    if country == "FI":  # Y-tunnus 1234567-8, mod-11 check digit
        if len(d) == 8 and _fi_check(d[:7]) == int(d[7]):
            return RegistryIdResult(f"FI:{d[:7]}-{d[7]}", f"FI{d}", None)
        return RegistryIdResult(None, None, f"invalid Finnish Y-tunnus '{raw}' (format/check digit)")
    if country == "SE":  # organisationsnummer NNNNNN-NNNN, Luhn
        if len(d) == 12 and d.startswith(("16",)):
            d = d[2:]
        if len(d) == 10 and _luhn_ok(d):
            return RegistryIdResult(f"SE:{d[:6]}-{d[6:]}", f"SE{d}01", None)
        return RegistryIdResult(None, None, f"invalid Swedish organisationsnummer '{raw}' (format/Luhn)")
    if country == "NO":  # organisasjonsnummer, 9 digits, mod-11
        if len(d) == 9 and _mod11_check(d[:8], (3, 2, 7, 6, 5, 4, 3, 2)) == int(d[8]):
            return RegistryIdResult(f"NO:{d}", f"NO{d}", None)
        return RegistryIdResult(None, None, f"invalid Norwegian organisasjonsnummer '{raw}' (format/mod-11)")
    if country == "DK":  # CVR, 8 digits (format check only; modulus rule not enforced for newer numbers)
        if len(d) == 8:
            return RegistryIdResult(f"DK:{d}", f"DK{d}", None)
        return RegistryIdResult(None, None, f"invalid Danish CVR '{raw}' (expected 8 digits)")
    if country == "EE":  # registrikood, 8 digits, two-pass mod-11 check digit (same scheme as isikukood)
        if len(d) == 8 and _ee_check(d[:7]) == int(d[7]):
            return RegistryIdResult(f"EE:{d}", None, None)
        return RegistryIdResult(None, None, f"invalid Estonian registry code '{raw}' (format/check digit)")
    if country == "IS":  # kennitala, 10 digits
        if len(d) == 10:
            return RegistryIdResult(f"IS:{d}", None, None)
        return RegistryIdResult(None, None, f"invalid Icelandic kennitala '{raw}'")
    if country == "CH":  # UID CHE-123.456.789, mod-11
        is_uid = raw.upper().replace(" ", "").startswith("CHE") and len(d) == 9
        if is_uid and _mod11_check(d[:8], (5, 4, 3, 2, 7, 6, 5, 4)) == int(d[8]):
            return RegistryIdResult(f"CH:CHE{d}", f"CHE{d}", None)
        return RegistryIdResult(None, None, f"invalid Swiss UID '{raw}' (format/check digit)")
    if country == "AT":  # Firmenbuchnummer FN 123456a
        m = re.fullmatch(r"(?:FN)?\s*(\d{1,6})\s*([a-zA-Z])", raw.replace(" ", ""), re.IGNORECASE)
        if m:
            return RegistryIdResult(f"AT:FN{m.group(1)}{m.group(2).lower()}", None, None)
        return RegistryIdResult(None, None, f"invalid Austrian Firmenbuchnummer '{raw}'")
    if country == "DE":  # HRB 12345 + registering court; numbers are only unique per court
        m = re.fullmatch(
            r"(HRA|HRB|GnR|PR|VR)\s*(\d+)(?:\s*([A-Z])(?=$|[\s,;]))?\s*(?:[,;]\s*|\s+)?(?:Amtsgericht\s+|AG\s+)?(.*)",
            raw,
            re.IGNORECASE,
        )
        if m:
            court = normalize_name(m.group(4) or "").upper().replace(" ", "")
            if not court:
                return RegistryIdResult(
                    None, None, f"German register number '{raw}' has no court; not usable as identity key"
                )
            suffix = (m.group(3) or "").upper()
            return RegistryIdResult(f"DE:{m.group(1).upper()}{m.group(2)}{suffix}:{court}", None, None)
        return RegistryIdResult(None, None, f"invalid German register number '{raw}'")
    return RegistryIdResult(None, None, f"registry IDs for country '{country}' are not supported")


_VAT_PATTERNS = {
    "FI": r"FI\d{8}",
    "SE": r"SE\d{12}",
    "NO": r"NO\d{9}",
    "DK": r"DK\d{8}",
    "DE": r"DE\d{9}",
    "AT": r"ATU\d{8}",
    "CH": r"CHE\d{9}",
    "EE": r"EE\d{9}",
}


def normalize_vat(country: str, value: str | None) -> tuple[str | None, str | None]:
    """Returns (canonical VAT key, warning)."""
    if not value or not value.strip():
        return None, None
    compact = re.sub(r"[\s.\-]", "", value.upper())
    compact = re.sub(r"(MVA|MWST|TVA|IVA)$", "", compact)
    pattern = _VAT_PATTERNS.get(country)
    if pattern and re.fullmatch(pattern, compact):
        return compact, None
    return None, f"invalid VAT ID '{value}' for {country}"


def registry_from_vat(country: str, vat: str) -> str | None:
    """Reverse of the deterministic registry->VAT derivation, where the national scheme allows it."""
    if country == "FI":
        return normalize_registry_id("FI", f"{vat[2:9]}-{vat[9]}").canonical
    if country == "SE":
        return normalize_registry_id("SE", vat[2:12]).canonical
    if country in ("NO", "DK"):
        return normalize_registry_id(country, vat[2:]).canonical
    if country == "CH":
        return normalize_registry_id("CH", vat).canonical
    return None


# --------------------------------------------------------------------------- numbers, contacts


def parse_employee_range(value: str | int | None) -> tuple[int, int | None] | None:
    """'35' -> (35, 35); '20-49' -> (20, 49); '250+' -> (250, None). Unparseable -> None (never guessed)."""
    if value is None:
        return None
    if isinstance(value, int):
        return (value, value) if value >= 0 else None
    text = strip_accents(str(value)).lower().strip()
    if not text or text in {"unknown", "n/a", "na", "-", "?"}:
        return None
    text = re.sub(r"(\d)[\s .](?=\d{3}\b)", r"\1", text)  # 1 200 / 1.200 thousands separators
    decimal = re.fullmatch(r"\D*(\d+(?:[\.,]\d+))\D*", text)
    if decimal:
        number = float(decimal.group(1).replace(",", "."))
        return (math.floor(number), math.ceil(number))
    m = re.fullmatch(r"\D*(\d+)\s*(?:-|–|—|to|bis)\s*(\d+)\D*", text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if lo <= hi else None
    m = re.fullmatch(r"\D*?(?:>=|≥|over|mehr als|uber)?\s*(\d+)\s*\+\D*", text) or re.fullmatch(
        r"\s*(?:>=|≥)\s*(\d+)\D*", text
    )
    if m:
        return (int(m.group(1)), None)
    m = re.fullmatch(r"\D*(\d+)\D*", text)
    if m:
        n = int(m.group(1))
        return (n, n)
    return None


def parse_money(value: str | int | None) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, int):
        return (value, value)
    text = str(value).strip().lower().replace(",", "").replace(" ", "")
    if not text:
        return None
    mult = 1
    if text.endswith(("m", "mio")):
        mult, text = 1_000_000, text.rstrip("mio").rstrip("m")
    elif text.endswith("k"):
        mult, text = 1_000, text[:-1]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:-(\d+(?:\.\d+)?))?", text)
    if not m:
        return None
    lo = int(float(m.group(1)) * mult)
    hi = int(float(m.group(2)) * mult) if m.group(2) else lo
    return (lo, hi) if lo <= hi else None


_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def normalize_email(value: str | None) -> tuple[str | None, str | None]:
    """Validates a source-supplied email. There is deliberately no function that constructs emails."""
    if not value or not value.strip():
        return None, None
    email = value.strip().lower()
    if _EMAIL_RE.fullmatch(email):
        return email, None
    return None, "contact email failed format validation and was dropped"


def normalize_phone(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) < 6:
        return None
    return ("+" if raw.startswith(("+", "00")) else "") + (digits[2:] if raw.startswith("00") else digits)


OWNERSHIP_TYPES = {"founder-led", "family-owned", "pe-backed", "corporate", "unknown"}


def normalize_ownership(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "pe": "pe-backed",
        "private-equity": "pe-backed",
        "family": "family-owned",
        "founder": "founder-led",
    }
    key = aliases.get(key, key)
    return key if key in OWNERSHIP_TYPES else None
