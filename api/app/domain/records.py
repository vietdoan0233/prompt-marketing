"""Connector-neutral parsed record + validation. Every connector's parse() yields ParsedRecord."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain import normalize as n

PARSER_VERSION = "parse-2026.09.1"

# Canonical input columns. Connectors map provider-specific headers/JSON onto these names.
COMPANY_FIELDS = [
    "legal_name",
    "trading_name",
    "country",
    "region",
    "city",
    "registry_id",
    "vat_id",
    "website",
    "industry_code",
    "sector",
    "employees",
    "revenue",
    "currency",
    "ownership_type",
    "description",
    # C-tier website signals (estimated): hiring volume and self-described leadership/ownership signals
    "open_positions",
    "founder_signal",
    "family_business_signal",
]
CONTACT_FIELDS = [
    "contact_name",
    "contact_role",
    "contact_email",
    "contact_phone",
    "contact_profile_url",
    "contact_basis",
]
META_FIELDS = ["source_key", "source_url", "observed_at"]
CANONICAL_FIELDS = COMPANY_FIELDS + CONTACT_FIELDS + META_FIELDS

# The field names used as company_facts.field_name
FACT_FIELDS = [
    "legal_name",
    "trading_name",
    "city",
    "website",
    "industry_code",
    "sector",
    "employees",
    "revenue",
    "ownership_type",
    "description",
    "registry_id",
    "vat_id",
    "open_positions",
    "founder_signal",
    "family_business_signal",
]
# Structured (non-string) row keys a connector may attach.
STRUCTURED_KEYS = {"contacts", "enrichment_only", "estimated_fields", "evidence", "warnings"}
CONTACT_BASES = {"public-business", "mergero-supplied", "partner-referral", "unknown"}


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


@dataclass
class ParsedContact:
    name: str
    role: str | None
    email: str | None
    phone: str | None
    profile_url: str | None
    contact_basis: str


@dataclass
class ParsedRecord:
    row_number: int
    source_key: str | None
    raw: dict[str, Any]  # company-only raw evidence (contact fields stripped)
    country: str | None = None
    region: str | None = None
    source_url: str | None = None
    observed_at: datetime | None = None
    registry_key: str | None = None
    vat_key: str | None = None
    derived_vat_key: str | None = None
    domain: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)  # field_name -> normalized value
    originals: dict[str, str] = field(default_factory=dict)  # field_name -> source value
    contacts: list[ParsedContact] = field(default_factory=list)
    enrichment_only: bool = False  # may only enrich an existing company, never create one
    fact_confidence: dict[str, str] = field(
        default_factory=dict
    )  # per-field override, e.g. estimated signals
    field_urls: dict[str, str] = field(default_factory=dict)  # per-field evidence URL
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return stable_hash(self.raw)

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_observed_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_record(
    row_number: int,
    row: dict[str, Any],
    *,
    source_countries: list[str],
    allowed_fields: list[str],
) -> ParsedRecord:
    """Normalize + validate one canonical row. Enforces the source's allowed-field list."""
    canonical: dict[str, Any] = {k: _clean(v) for k, v in row.items() if k in CANONICAL_FIELDS}
    unknown_cols = sorted(
        k for k in row if k not in CANONICAL_FIELDS and k not in STRUCTURED_KEYS and _clean(row.get(k))
    )
    rec = ParsedRecord(row_number=row_number, source_key=canonical.get("source_key"), raw={})
    rec.enrichment_only = bool(row.get("enrichment_only"))
    rec.warnings.extend(str(w) for w in row.get("warnings") or [])
    rec.field_urls = {k: str(v) for k, v in (row.get("evidence") or {}).items()}
    rec.fact_confidence = {f: "estimated" for f in row.get("estimated_fields") or []}

    if unknown_cols:
        rec.warnings.append(f"ignored unmapped columns: {', '.join(unknown_cols)}")

    # Allowed-field enforcement: values for fields the source may not supply are dropped, not stored.
    allowed = set(allowed_fields) | set(META_FIELDS) | {"country"}
    contacts_allowed = "contacts" in allowed
    for key in list(canonical):
        if canonical[key] is None:
            continue
        is_contact = key in CONTACT_FIELDS
        if (is_contact and not contacts_allowed) or (not is_contact and key not in allowed):
            rec.warnings.append(f"field '{key}' is not allowed for this source and was dropped")
            canonical[key] = None

    rec.raw = {k: v for k, v in canonical.items() if v is not None and k not in CONTACT_FIELDS}
    if rec.field_urls:
        rec.raw["evidence"] = rec.field_urls

    legal_name = canonical.get("legal_name")
    if not legal_name and not rec.enrichment_only:
        rec.errors.append("missing required field 'legal_name'")

    country = n.normalize_country(canonical.get("country"))
    if not country:
        rec.errors.append(f"missing or unrecognised country '{canonical.get('country') or ''}'")
    elif country not in source_countries:
        rec.errors.append(f"country {country} is outside this source's approved coverage {source_countries}")
    rec.country = country
    rec.region = n.region_for_country(country)
    rec.source_url = canonical.get("source_url")

    if canonical.get("observed_at"):
        rec.observed_at = parse_observed_at(canonical["observed_at"])
        if rec.observed_at is None:
            rec.warnings.append(f"unparseable observed_at '{canonical['observed_at']}'; using retrieval time")

    if rec.errors:
        return rec

    assert country is not None
    facts, originals = rec.facts, rec.originals

    def put(name: str, value: Any, original: str | None) -> None:
        facts[name] = value
        if original is not None:
            originals[name] = original

    if legal_name:
        put("legal_name", legal_name.strip(), legal_name)
    for simple in ("trading_name", "city", "sector", "description"):
        if canonical.get(simple):
            put(simple, " ".join(canonical[simple].split()), canonical[simple])
    if canonical.get("industry_code"):
        put("industry_code", canonical["industry_code"].replace(" ", ""), canonical["industry_code"])

    reg = n.normalize_registry_id(country, canonical.get("registry_id"))
    if reg.warning:
        rec.warnings.append(reg.warning)
    if reg.canonical:
        rec.registry_key, rec.derived_vat_key = reg.canonical, reg.derived_vat
        put("registry_id", reg.canonical.split(":", 1)[1], canonical.get("registry_id"))

    vat, vat_warning = n.normalize_vat(country, canonical.get("vat_id"))
    if vat_warning:
        rec.warnings.append(vat_warning)
    if vat:
        rec.vat_key = vat
        put("vat_id", vat, canonical.get("vat_id"))
        from_vat = n.registry_from_vat(country, vat)
        if rec.registry_key and from_vat and from_vat != rec.registry_key:
            rec.warnings.append(f"VAT ID {vat} does not correspond to registry ID {rec.registry_key}")

    if canonical.get("website"):
        domain = n.normalize_domain(canonical["website"])
        if not domain:
            rec.warnings.append(f"unparseable website '{canonical['website']}'")
        else:
            if not rec.enrichment_only:  # a site cannot vouch for its own URL; it is an identity key only
                put("website", f"https://{domain}", canonical["website"])
            if n.is_generic_domain(domain):
                rec.warnings.append(f"domain {domain} is a shared host and is not used as an identity key")
            else:
                rec.domain = domain

    if canonical.get("employees"):
        rng = n.parse_employee_range(canonical["employees"])
        if rng is None:
            rec.warnings.append(f"unparseable employee count '{canonical['employees']}'; left unknown")
        else:
            put("employees", {"min": rng[0], "max": rng[1]}, canonical["employees"])
    elif not rec.enrichment_only:
        rec.warnings.append("no headcount evidence; qualification is unknown")

    if canonical.get("open_positions"):
        try:
            count = int(canonical["open_positions"])
            if count >= 0:
                put("open_positions", count, canonical["open_positions"])
        except ValueError:
            rec.warnings.append(f"unparseable open_positions '{canonical['open_positions']}'")
    for flag in ("founder_signal", "family_business_signal"):
        if canonical.get(flag):
            put(flag, canonical[flag].lower() in {"true", "1", "yes"}, canonical[flag])

    if canonical.get("revenue"):
        money = n.parse_money(canonical["revenue"])
        currency = (canonical.get("currency") or "").upper() or None
        if money is None:
            rec.warnings.append(f"unparseable revenue '{canonical['revenue']}'")
        elif not currency:
            rec.warnings.append("revenue without currency was dropped")
        else:
            put("revenue", {"min": money[0], "max": money[1], "currency": currency}, canonical["revenue"])

    if canonical.get("ownership_type"):
        own = n.normalize_ownership(canonical["ownership_type"])
        if own == "unknown":
            pass  # a source saying "unknown" is not a claim; the field stays missing
        elif own:
            put("ownership_type", own, canonical["ownership_type"])
        else:
            rec.warnings.append(f"unknown ownership type '{canonical['ownership_type']}'; left unknown")

    if not (rec.registry_key or rec.vat_key or rec.domain):
        rec.warnings.append(
            "no stable identity key (registry ID, VAT ID or domain); matched by source key only"
        )

    if not rec.source_key:
        rec.source_key = (
            rec.registry_key
            or (f"vat:{rec.vat_key}" if rec.vat_key else None)
            or (f"domain:{rec.domain}" if rec.domain else f"row:{stable_hash(rec.raw)[:16]}")
        )

    contact_rows = [canonical] + [
        {k: _clean(v) for k, v in c.items() if k in CONTACT_FIELDS} for c in row.get("contacts") or []
    ]
    if row.get("contacts") and not contacts_allowed:
        rec.warnings.append("field 'contacts' is not allowed for this source and was dropped")
        contact_rows = [canonical]
    for c in contact_rows:
        if c.get("contact_name"):
            rec.contacts.append(_parse_contact(c, rec))
        elif any(c.get(f) for f in ("contact_email", "contact_phone")):
            rec.warnings.append("contact details without a contact name were dropped")

    return rec


def _parse_contact(c: dict[str, Any], rec: ParsedRecord) -> ParsedContact:
    email, email_warning = n.normalize_email(c.get("contact_email"))
    if email_warning:
        rec.warnings.append(email_warning)
    basis = (c.get("contact_basis") or "unknown").lower()
    if basis not in CONTACT_BASES:
        rec.warnings.append(f"unknown contact_basis '{basis}'; stored as 'unknown'")
        basis = "unknown"
    return ParsedContact(
        name=" ".join(str(c["contact_name"]).split()),
        role=c.get("contact_role"),
        email=email,
        phone=n.normalize_phone(c.get("contact_phone")),
        profile_url=c.get("contact_profile_url"),
        contact_basis=basis,
    )
