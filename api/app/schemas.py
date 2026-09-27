"""Typed API schemas. All external input is validated here; datetimes serialize as ISO 8601 UTC."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.permissions import PERMISSION_STATUSES, SOURCE_MODES


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ sources


class SourceOut(ORM):
    id: str
    name: str
    tier: str
    provider: str
    region: str
    countries: list[str]
    source_type: str
    source_mode: str
    terms_url: str | None
    permission_status: str
    approval_reference: str | None
    connector_type: str
    connector_config: dict[str, Any]
    enabled: bool
    allowed_fields: list[str]
    field_mapping: dict[str, str]
    base_confidence: str
    usage_policy: str
    retention_days: int
    rate_limit_per_minute: int | None
    trust_rank: int
    notes: str | None
    created_at: datetime
    updated_at: datetime
    ingestible: bool = False
    gate_reasons: list[str] = []


class SourceCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{2,63}$")
    name: str = Field(min_length=2, max_length=200)
    provider: str = Field(min_length=2, max_length=200)
    region: Literal["baltics", "internal"]
    countries: list[Literal["EE"]] = Field(min_length=1)
    source_type: Literal["registry", "licensed_feed", "mergero_csv"]
    source_mode: str
    terms_url: str | None = None
    connector_type: Literal["csv"] = "csv"
    allowed_fields: list[str] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    base_confidence: Literal["verified", "estimated"] = "estimated"
    usage_policy: Literal["internal-only", "restricted"] = "internal-only"
    retention_days: int = Field(default=30, ge=0, le=3650)
    trust_rank: int = Field(default=50, ge=1, le=99)
    notes: str | None = None

    @field_validator("source_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in SOURCE_MODES:
            raise ValueError(f"source_mode must be one of {sorted(SOURCE_MODES)}")
        return v


class PermissionChange(BaseModel):
    permission_status: str
    approval_reference: str | None = Field(default=None, max_length=300)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("permission_status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in PERMISSION_STATUSES:
            raise ValueError(f"permission_status must be one of {sorted(PERMISSION_STATUSES)}")
        return v


# ------------------------------------------------------------------ ingestion


class DiscoveryRunCreate(BaseModel):
    source_id: str
    query: dict[str, Any] = Field(default_factory=dict)
    min_employees: int | None = Field(default=None, ge=0, le=100000)


class IngestionRecordOut(ORM):
    row_number: int
    source_key: str | None
    outcome: str
    company_id: str | None
    qualification: str | None
    match_reasons: list[str]
    warnings: list[str]
    errors: list[str]


class IngestionRunOut(ORM):
    id: str
    source_id: str
    status: str
    kind: str
    file_name: str | None
    query: dict[str, Any]
    region: str | None
    input_hash: str | None
    input_retained: bool = False
    input_expires_at: datetime | None
    parser_version: str
    config_hash: str
    min_employees: int
    counts: dict[str, int]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    retry_of_id: str | None
    actor: str
    started_at: datetime
    finished_at: datetime | None


class IngestionRunDetail(IngestionRunOut):
    records: list[IngestionRecordOut]


# ------------------------------------------------------------------ companies


class FactOut(ORM):
    id: str
    field_name: str
    value_json: Any
    original_value: str | None
    code_system: str | None
    code_version: str | None
    source_id: str
    source_name: str | None = None
    source_key: str | None
    source_url: str | None
    ingestion_run_id: str | None
    snapshot_id: str | None
    observed_at: datetime
    valid_from: datetime
    valid_to: datetime | None
    base_confidence: str
    confidence: str
    usage_policy: str
    review_status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    is_correction: bool
    corrects_fact_id: str | None
    correction_reason: str | None


class FieldView(BaseModel):
    field_name: str
    value: Any
    status: str  # verified | multi-source | estimated | old | conflicting | unknown | manually-corrected
    label: (
        str  # source-verified | multi-source | estimated | manually-corrected | conflicting | old | unknown
    )
    source_ids: list[str]
    supporting_fact_ids: list[str]
    conflicting_values: list[Any]


class ContactOut(ORM):
    id: str
    name: str
    role: str | None
    email: str | None
    phone: str | None
    profile_url: str | None
    country: str | None
    language: str | None
    source_id: str
    source_url: str | None
    confidence: str
    contact_basis: str
    usage_policy: str
    last_verified_at: datetime | None


class IdentifierOut(ORM):
    kind: str
    value: str
    source_id: str | None
    derived: bool


class FinancialOut(ORM):
    id: str
    period_start: date | None
    period_end: date | None
    period_days: int | None
    period_length_class: str | None
    fiscal_year: int
    currency: str | None
    revenue: Decimal | None
    ebitda: Decimal | None
    net_income: Decimal | None
    dividends: Decimal | None
    capex: Decimal | None
    depreciation: Decimal | None
    depreciation_and_impairment: Decimal | None
    operating_profit: Decimal | None
    profit_before_tax: Decimal | None
    total_assets: Decimal | None
    equity: Decimal | None
    labour_cost: Decimal | None
    employees_fte: Decimal | None
    source_id: str
    source_name: str | None = None
    source_url: str
    source_file: str | None
    snapshot_id: str | None
    observed_at: datetime
    confidence: str
    usage_policy: str
    parser_version: str
    statement_scope: str | None
    value_type: str
    filing_id: str
    document_id: str | None
    unit: str | None
    restated: bool | None
    calculation_formula: str | None
    ingestion_run_id: str | None
    review_status: str
    registry_code: str
    source_key: str
    content_hash: str
    source_values: list[dict[str, Any]]


class ShareholderOut(ORM):
    id: str
    holder_type: str  # person | legal_entity | unknown
    holder_name: str
    holder_registry_code: str | None
    holder_country: str | None
    role: str | None
    holding_amount: Decimal | None
    holding_currency: str | None
    holding_percent: Decimal | None
    holding_type: str | None
    effective_from: date | None
    effective_to: date | None
    source_id: str
    source_name: str | None = None
    source_url: str
    source_file: str | None
    snapshot_id: str | None
    ingestion_run_id: str | None
    observed_at: datetime
    parser_version: str
    content_hash: str
    valid_from: datetime
    valid_to: datetime | None


class RegisteredAddressOut(ORM):
    id: str
    address_line: str | None
    postal_code: str | None
    city: str | None
    municipality: str | None
    county: str | None
    ehak_code: str | None
    country: str | None
    source_id: str
    source_name: str | None = None
    source_url: str
    source_file: str | None
    snapshot_id: str | None
    ingestion_run_id: str | None
    observed_at: datetime
    parser_version: str
    content_hash: str
    warnings: list[str]
    valid_from: datetime
    valid_to: datetime | None


class IndustryCodeOut(BaseModel):
    code: str
    code_system: str | None
    code_version: str | None


class CompanySummary(ORM):
    id: str
    legal_name: str
    registry_status: str | None = None
    trading_name: str | None
    country: str
    region: str | None
    city: str | None
    website: str | None
    sector: str | None
    industry_codes: list[str]
    industry_code_details: list[IndustryCodeOut] = []
    # Display-only two-digit EMTAK division label derived from `industry_codes`; `sector` itself stays the
    # untouched source field.
    sector_label: str | None = None
    estimated_employee_min: int | None
    estimated_employee_max: int | None
    ownership_type: str
    qualification_status: str
    review_status: str
    first_seen_at: datetime
    last_verified_at: datetime | None
    freshness: str = "unknown"  # fresh | aging | stale | unknown
    source_ids: list[str] = []
    source_count: int = 0
    multi_source_fields: int = 0
    conflict_fields: int = 0
    completeness: int = 0
    headcount_status: str = "unknown"
    registry_id: str | None = None
    open_positions: int | None = None
    founder_signal: bool = False
    family_business_signal: bool = False
    website_enriched: bool = False


class CompanyPage(BaseModel):
    items: list[CompanySummary]
    total: int
    page: int
    page_size: int
    filters: dict[str, Any]


class SellerProspectOut(BaseModel):
    company_id: str
    legal_name: str
    registry_id: str | None
    registry_status: str | None
    sector: str | None
    peer_group: str | None
    peer_group_label: str | None = None
    focus_band: str
    quality_band: str
    evidence_status: str
    next_action: str
    latest_year: int | None
    latest_revenue_eur: float | None
    latest_operating_margin: float | None
    three_year_median_margin: float | None
    three_year_revenue_cagr: float | None
    stable_revenue: bool | None
    positive_profit_years: int | None
    latest_equity_ratio: float | None
    peer_count: int | None = None
    margin_peer_z: float | None = None
    equity_peer_z: float | None = None
    financial_profile_index: float | None = None
    buyer_fit: Literal["not_assessed"] = "not_assessed"
    owner_intent: Literal["unknown"] = "unknown"
    latest_employees_fte: float | None = None
    consolidated_revenue_eur: float | None = None
    # group_parent: also files consolidated accounts; holding_activity: EMTAK 64.2x/70.10 activity code;
    # cash_harvesting_candidate: see cash_harvesting_candidate/cash_harvesting_evidence_status below
    flags: list[Literal["group_parent", "holding_activity", "cash_harvesting_candidate"]] = []
    # Cash Harvesting candidate: a separate, explainable financial review signal. Never derived from
    # dividends or capex. Triggers only from populated, comparable revenue CAGR and EBITDA margin; a
    # missing or incomparable input keeps evidence_status "insufficient_evidence" and never trips the flag.
    cash_harvesting_candidate: bool = False
    cash_harvesting_evidence_status: Literal["insufficient_evidence", "evaluated"] = "insufficient_evidence"
    latest_ebitda_margin: float | None = None
    cash_harvesting_revenue_cagr: float | None = None
    # Link to the official e-Business Register company page, built from the source-backed registry code.
    registry_url: str | None = None
    # The opt-in web-digital-decay verdict, read from the company's latest CompanyFact if one was run.
    # None means no check has been run yet, not that the company is inactive or a poor fit.
    digital_decay_verdict: (
        Literal["coasting", "decaying", "watch", "active", "insufficient_evidence"] | None
    ) = None
    digital_decay_observed_at: datetime | None = None
    # Deterministic brief: what the filings show (each line cites a fiscal year) and what they cannot show.
    review_reasons: list[str] = []
    open_questions: list[str] = []
    filing_ids: list[str]
    source_urls: list[str]
    issues: list[str]


class FunnelStageOut(BaseModel):
    key: str
    label: str
    count: int
    rule: str


class PeerGroupOut(BaseModel):
    group: str
    label: str
    peer_count: int
    median_margin: float
    median_equity_ratio: float


class SectorOptionOut(BaseModel):
    """One sector-filter option: a source-backed two-digit EMTAK division with at least
    `MIN_COMPANIES_PER_SECTOR` companies (see app.domain.sectors), or the display-only "others" bucket."""

    code: str
    label: str
    count: int


class SellerFunnelOut(BaseModel):
    total_companies: int
    core_size: int
    three_year_profitable: int
    advisor_review: int
    # Of the advisor-review queue: how many already carry a digital-decay verdict, and how many of those
    # are coasting or decaying (a persisted signal read at query time; running new checks is a separate step).
    advisor_review_decay_checked: int = 0
    advisor_review_decay_flagged: int = 0
    # Which list `items` holds: "cash_harvesting" (default: Cash Harvesting candidates not in liquidation,
    # bankrupt or deleted) or "all" (every imported company in the sector, the pre-signal list).
    view: Literal["cash_harvesting", "all"] = "all"
    hide_active_decay: bool = False
    # Companies in the selected view after every filter, before `limit` truncates `items`.
    listed_companies: int = 0
    # Cash Harvesting candidates not in liquidation, bankrupt or deleted, within the sector filter.
    cash_harvesting_candidates: int = 0
    # Of the listed companies: how many carry any digital-decay verdict / a coasting, decaying or watch
    # verdict.
    listed_decay_checked: int = 0
    listed_decay_flagged: int = 0
    stages: list[FunnelStageOut] = []
    peer_groups: list[PeerGroupOut] = []
    sector_options: list[SectorOptionOut] = []
    items: list[SellerProspectOut]
    methodology: str


class DuplicateOut(ORM):
    id: str
    company_a_id: str
    company_b_id: str
    company_a_name: str | None = None
    company_b_name: str | None = None
    score: int
    band: str
    reasons: list[dict[str, Any]]
    status: str
    resolved_by: str | None
    resolved_at: datetime | None
    resolution_reason: str | None
    created_at: datetime


class AuditEventOut(ORM):
    id: str
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str | None
    company_id: str | None
    details: dict[str, Any]


class TimelineEntry(BaseModel):
    at: datetime
    kind: str  # fact | correction | audit
    title: str
    source_id: str | None = None
    detail: dict[str, Any] = {}


class DigitalDecayOut(BaseModel):
    """Latest Digital Decay website activity signal (an estimated, source-backed fact) with provenance."""

    signal: dict[str, Any]
    fact_id: str
    source_id: str
    source_name: str | None = None
    source_url: str | None
    ingestion_run_id: str | None
    snapshot_id: str | None
    observed_at: datetime
    confidence: str
    review_status: str
    domain_identifier: str | None


class DigitalDecayRunCreate(BaseModel):
    domain: str | None = Field(default=None, max_length=253)


class DigitalDecayRunOut(BaseModel):
    run_id: str
    status: str
    signal: dict[str, Any] | None
    run: IngestionRunDetail
    digital_decay: DigitalDecayOut | None


class CompanyDetail(BaseModel):
    company: CompanySummary
    description: str | None
    revenue: dict[str, Any] | None
    merged_into_id: str | None
    fields: list[FieldView]
    facts: list[FactOut]
    history: list[FactOut]
    identifiers: list[IdentifierOut]
    financials: list[FinancialOut]
    registered_address: RegisteredAddressOut | None
    shareholders: list[ShareholderOut]
    contacts: list[ContactOut]
    duplicates: list[DuplicateOut]
    audit_events: list[AuditEventOut]
    timeline: list[TimelineEntry]
    warnings: list[str]
    digital_decay: DigitalDecayOut | None = None


class CorrectionCreate(BaseModel):
    field_name: str
    value: Any
    reason: str = Field(min_length=3, max_length=2000)
    corrects_fact_id: str | None = None
    evidence_url: str | None = Field(default=None, max_length=500)


class ReviewUpdate(BaseModel):
    review_status: Literal["unreviewed", "reviewed", "needs_correction"]
    note: str | None = Field(default=None, max_length=2000)


class ContactErasure(BaseModel):
    request_reference: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="GDPR erasure request", max_length=1000)


class DuplicateMerge(BaseModel):
    survivor_id: str
    reason: str = Field(min_length=3, max_length=2000)


class DuplicateResolve(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
