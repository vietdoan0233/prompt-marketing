"""Typed API schemas. All external input is validated here; datetimes serialize as ISO 8601 UTC."""

from datetime import datetime
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
    region: Literal["nordics", "dach", "internal"]
    countries: list[Literal["FI", "SE", "NO", "DK", "IS", "DE", "AT", "CH"]] = Field(min_length=1)
    source_type: Literal["registry", "website", "licensed_feed", "mergero_csv"]
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


class CompanySummary(ORM):
    id: str
    legal_name: str
    trading_name: str | None
    country: str
    region: str | None
    city: str | None
    website: str | None
    sector: str | None
    industry_codes: list[str]
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


class CompanyDetail(BaseModel):
    company: CompanySummary
    description: str | None
    revenue: dict[str, Any] | None
    merged_into_id: str | None
    fields: list[FieldView]
    facts: list[FactOut]
    history: list[FactOut]
    identifiers: list[IdentifierOut]
    contacts: list[ContactOut]
    duplicates: list[DuplicateOut]
    audit_events: list[AuditEventOut]
    timeline: list[TimelineEntry]
    warnings: list[str]


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
