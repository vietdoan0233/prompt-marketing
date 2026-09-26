"""Relational model. Facts are append-only/versioned; display fields on `companies` are a derived view."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Stores UTC; always returns timezone-aware datetimes (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(32))  # production: baltics | internal
    countries: Mapped[list[str]] = mapped_column(JsonType, default=list)
    tier: Mapped[str] = mapped_column(
        String(1), default="A"
    )  # A official | L licensed | C company website | I internal
    source_type: Mapped[str] = mapped_column(
        String(32)
    )  # registry | website | licensed_feed | mergero_csv | manual
    source_mode: Mapped[str] = mapped_column(String(32))  # public-approved | licensed | mergero-supplied
    terms_url: Mapped[str | None] = mapped_column(String(500))
    permission_status: Mapped[str] = mapped_column(String(32), default="pending")
    approval_reference: Mapped[str | None] = mapped_column(String(300))
    connector_type: Mapped[str] = mapped_column(String(32))  # csv | ee_ariregister | manual
    connector_config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_fields: Mapped[list[str]] = mapped_column(JsonType, default=list)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JsonType, default=dict)
    base_confidence: Mapped[str] = mapped_column(String(32), default="verified")
    usage_policy: Mapped[str] = mapped_column(String(64), default="internal-only")
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    trust_rank: Mapped[int] = mapped_column(Integer, default=50)  # lower = more authoritative
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(32), default="IMPORT_STARTED")
    kind: Mapped[str] = mapped_column(String(32))  # csv | discovery
    file_name: Mapped[str | None] = mapped_column(String(300))
    query: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    region: Mapped[str | None] = mapped_column(String(32))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    input_text: Mapped[str | None] = mapped_column(
        Text
    )  # raw input kept only until retention expiry (for retry)
    input_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    parser_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(64))
    min_employees: Mapped[int] = mapped_column(Integer, default=20)
    counts: Mapped[dict[str, int]] = mapped_column(JsonType, default=dict)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    retry_of_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    actor: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    source: Mapped[Source] = relationship()
    records: Mapped[list["IngestionRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="IngestionRecord.row_number"
    )


class IngestionRecord(Base):
    """Per-record outcome of a run, so every accepted/rejected/duplicate decision is explainable."""

    __tablename__ = "ingestion_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    source_key: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(32))  # accepted | updated | unchanged | duplicate | rejected
    company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    qualification: Mapped[str | None] = mapped_column(String(32))
    match_reasons: Mapped[list[str]] = mapped_column(JsonType, default=list)
    warnings: Mapped[list[str]] = mapped_column(JsonType, default=list)
    errors: Mapped[list[str]] = mapped_column(JsonType, default=list)

    run: Mapped[IngestionRun] = relationship(back_populates="records")


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "source_key", "content_hash", name="uq_snapshot_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    source_key: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(500))
    request_id: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    # Minimum evidence to reproduce the record. Personal contact fields are never stored here.
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    parser_version: Mapped[str] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    legal_name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    trading_name: Mapped[str | None] = mapped_column(String(300))
    country: Mapped[str] = mapped_column(String(2), index=True)
    region: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(300))
    industry_codes: Mapped[list[str]] = mapped_column(JsonType, default=list)
    sector: Mapped[str | None] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    estimated_employee_min: Mapped[int | None] = mapped_column(Integer)
    estimated_employee_max: Mapped[int | None] = mapped_column(Integer)
    revenue_min: Mapped[int | None] = mapped_column(Integer)
    revenue_max: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    ownership_type: Mapped[str] = mapped_column(String(32), default="unknown")
    qualification_status: Mapped[str] = mapped_column(String(32), default="unknown_headcount", index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed", index=True)
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    identifiers: Mapped[list["CompanyIdentifier"]] = relationship(back_populates="company")
    facts: Mapped[list["CompanyFact"]] = relationship(back_populates="company")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="company")


class CompanyIdentifier(Base):
    """Stable identity keys, stored separately from display fields so resolution can be rerun."""

    __tablename__ = "company_identifiers"
    __table_args__ = (UniqueConstraint("kind", "value", name="uq_identifier_kind_value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # registry_id | vat_id | domain | source_key
    value: Mapped[str] = mapped_column(String(300))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    derived: Mapped[bool] = mapped_column(Boolean, default=False)  # e.g. VAT derived from FI Y-tunnus
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    company: Mapped[Company] = relationship(back_populates="identifiers")


class CompanyFact(Base):
    __tablename__ = "company_facts"
    __table_args__ = (Index("ix_fact_company_field", "company_id", "field_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    field_name: Mapped[str] = mapped_column(String(64))
    value_json: Mapped[Any] = mapped_column(JsonType)
    original_value: Mapped[str | None] = mapped_column(Text)  # source value before normalization
    code_system: Mapped[str | None] = mapped_column(String(32))
    code_version: Mapped[str | None] = mapped_column(String(64))
    value_hash: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_key: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(500))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("source_snapshots.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime)  # set when superseded by the same source
    base_confidence: Mapped[str] = mapped_column(String(32))  # what the source itself asserts
    confidence: Mapped[str] = mapped_column(
        String(32)
    )  # verified|multi-source|estimated|old|conflicting|unknown
    usage_policy: Mapped[str] = mapped_column(String(64))
    review_status: Mapped[str] = mapped_column(String(32), default="unreviewed")
    reviewed_by: Mapped[str | None] = mapped_column(String(200))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False)
    corrects_fact_id: Mapped[str | None] = mapped_column(ForeignKey("company_facts.id"))
    correction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    company: Mapped[Company] = relationship(back_populates="facts")
    source: Mapped[Source] = relationship()


class Contact(Base):
    """Business contact = personal data. Source-backed only; never enriched or guessed."""

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(64))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    country: Mapped[str | None] = mapped_column(String(2))
    language: Mapped[str | None] = mapped_column(String(8))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_key: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(500))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    confidence: Mapped[str] = mapped_column(String(32), default="verified")
    contact_basis: Mapped[str] = mapped_column(String(32), default="unknown")
    usage_policy: Mapped[str] = mapped_column(String(64), default="internal-only")
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    company: Mapped[Company] = relationship(back_populates="contacts")


class ContactSuppression(Base):
    """Tombstone after GDPR erasure: a one-way hash so re-imports do not resurrect the person."""

    __tablename__ = "contact_suppressions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_reference: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (UniqueConstraint("company_a_id", "company_b_id", name="uq_duplicate_pair"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_a_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    company_b_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    score: Mapped[int] = mapped_column(Integer)  # 0-100
    band: Mapped[str] = mapped_column(String(16))  # possible | likely
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | merged | dismissed | linked
    resolved_by: Mapped[str | None] = mapped_column(String(200))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(300))
    company_id: Mapped[str | None] = mapped_column(String(36), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


Money = Numeric(20, 2)


class CompanyFinancial(Base):
    """One row per company, filing and statement scope, with source-period length made explicit.

    Reported values retain their source provenance. When EBITDA is absent but operating profit and
    depreciation/impairment are both reported, the Estonia importer derives EBITDA from operating profit
    minus the signed depreciation/impairment source line. This adds back negative reported expenses and
    records the formula with value_type='derived'. Reported EBITDA always takes precedence. Values are
    never annualized: consumers should use period_days and period_length_class when comparing filings.
    """

    __tablename__ = "company_financials"
    __table_args__ = (
        UniqueConstraint("source_id", "source_key", "content_hash", name="uq_financial_source_version"),
        Index("ix_financial_company_year", "company_id", "fiscal_year"),
        Index("ix_financial_source_key", "source_id", "source_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    period_days: Mapped[int | None] = mapped_column(Integer)
    period_length_class: Mapped[str | None] = mapped_column(String(24))
    fiscal_year: Mapped[int] = mapped_column(Integer, index=True)
    currency: Mapped[str | None] = mapped_column(String(3))
    revenue: Mapped[Decimal | None] = mapped_column(Money)
    ebitda: Mapped[Decimal | None] = mapped_column(Money)
    net_income: Mapped[Decimal | None] = mapped_column(Money)
    dividends: Mapped[Decimal | None] = mapped_column(Money)
    capex: Mapped[Decimal | None] = mapped_column(Money)
    depreciation: Mapped[Decimal | None] = mapped_column(Money)
    # Additional explicitly reported measures (nullable canonical fields)
    depreciation_and_impairment: Mapped[Decimal | None] = mapped_column(Money)  # "kulum ja väärtuse langus"
    operating_profit: Mapped[Decimal | None] = mapped_column(Money)
    profit_before_tax: Mapped[Decimal | None] = mapped_column(Money)
    total_assets: Mapped[Decimal | None] = mapped_column(Money)
    equity: Mapped[Decimal | None] = mapped_column(Money)
    labour_cost: Mapped[Decimal | None] = mapped_column(Money)
    employees_fte: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_url: Mapped[str] = mapped_column(String(500))
    source_file: Mapped[str | None] = mapped_column(String(300))  # dataset file identity
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("source_snapshots.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    confidence: Mapped[str] = mapped_column(String(32))
    usage_policy: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    statement_scope: Mapped[str | None] = mapped_column(
        String(16)
    )  # standalone | consolidated | NULL (unproven)
    value_type: Mapped[str] = mapped_column(String(16), default="reported")  # reported | derived
    filing_id: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(16))
    restated: Mapped[bool | None] = mapped_column(Boolean)
    calculation_formula: Mapped[str | None] = mapped_column(Text)
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    review_status: Mapped[str] = mapped_column(
        String(32), default="unreviewed"
    )  # unreviewed | superseded | ...
    registry_code: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(200))  # registry code + report_id + period + scope
    content_hash: Mapped[str] = mapped_column(String(64))
    source_values: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)  # original lines
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RegisteredAddress(Base):
    """The registered seat (asukoht) from the official register, not an operating location.

    Versioned: an unchanged address (same content hash) never creates a new row; a changed one closes the
    previous row (valid_to) and adds a new one.
    """

    __tablename__ = "registered_addresses"
    __table_args__ = (
        Index("ix_registered_address_current", "company_id", "valid_to"),
        Index("ix_registered_address_hash", "company_id", "source_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    address_line: Mapped[str | None] = mapped_column(String(300))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(120))
    municipality: Mapped[str | None] = mapped_column(String(120))
    county: Mapped[str | None] = mapped_column(String(120))
    ehak_code: Mapped[str | None] = mapped_column(String(8))
    country: Mapped[str | None] = mapped_column(String(2))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_url: Mapped[str] = mapped_column(String(500))
    source_file: Mapped[str | None] = mapped_column(String(300))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("source_snapshots.id", ondelete="SET NULL"))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    parser_version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    warnings: Mapped[list[str]] = mapped_column(JsonType, default=list)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CompanyShareholder(Base):
    """Current shareholders (osanikud) from the official register.

    A person shareholder is stored by name, role and holding only. The official file's national ID code,
    its one-way hash, birth date and home address are personal data and are never read by the importer, so
    they can never reach this table or a source snapshot. A legal-entity shareholder keeps its registry (or,
    for a foreign entity, its foreign) code, which identifies a company, not a person.

    Versioned like RegisteredAddress, but as one group per company: the whole reported shareholder set is
    replaced together when any part of it changes, since the set — not any single holder — is what the
    source asserts as current.
    """

    __tablename__ = "company_shareholders"
    __table_args__ = (
        Index("ix_company_shareholder_current", "company_id", "valid_to"),
        Index("ix_company_shareholder_hash", "company_id", "source_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    holder_type: Mapped[str] = mapped_column(String(16))  # person | legal_entity | unknown
    holder_name: Mapped[str] = mapped_column(String(300))
    holder_registry_code: Mapped[str | None] = mapped_column(String(32))  # legal entities only
    holder_country: Mapped[str | None] = mapped_column(String(8))  # foreign legal entities only
    role: Mapped[str | None] = mapped_column(String(64))  # e.g. "Osanik"
    holding_amount: Mapped[Decimal | None] = mapped_column(Money)
    holding_currency: Mapped[str | None] = mapped_column(String(3))
    holding_percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    holding_type: Mapped[str | None] = mapped_column(String(64))  # e.g. "Ainuomand" (sole ownership)
    effective_from: Mapped[date | None] = mapped_column(Date)  # registry-entry date for this holding
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    source_url: Mapped[str] = mapped_column(String(500))
    source_file: Mapped[str | None] = mapped_column(String(300))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("source_snapshots.id", ondelete="SET NULL"))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    parser_version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))  # of the whole reported set, shared by its rows
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
