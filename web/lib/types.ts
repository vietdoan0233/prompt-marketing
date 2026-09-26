// Mirrors api/app/schemas.py. Keep in sync when the API contract changes.

export type Source = {
  id: string;
  name: string;
  tier: "A" | "L" | "C" | "I";
  provider: string;
  region: string;
  countries: string[];
  source_type: string;
  source_mode: string;
  terms_url: string | null;
  permission_status: string;
  approval_reference: string | null;
  connector_type: string;
  connector_config: Record<string, unknown>;
  enabled: boolean;
  allowed_fields: string[];
  field_mapping: Record<string, string>;
  base_confidence: string;
  usage_policy: string;
  retention_days: number;
  rate_limit_per_minute: number | null;
  trust_rank: number;
  notes: string | null;
  ingestible: boolean;
  gate_reasons: string[];
};

export type IngestionRecord = {
  row_number: number;
  source_key: string | null;
  outcome: string;
  company_id: string | null;
  qualification: string | null;
  match_reasons: string[];
  warnings: string[];
  errors: string[];
};

export type IngestionRun = {
  id: string;
  source_id: string;
  status: string;
  kind: string;
  file_name: string | null;
  query: Record<string, unknown>;
  region: string | null;
  input_hash: string | null;
  input_retained: boolean;
  input_expires_at: string | null;
  parser_version: string;
  config_hash: string;
  min_employees: number;
  counts: Record<string, number>;
  warnings: { row?: number; source_key?: string; message: string }[];
  errors: { stage?: string; message: string; reference?: string }[];
  retry_of_id: string | null;
  actor: string;
  started_at: string;
  finished_at: string | null;
  records?: IngestionRecord[];
};

export type CompanySummary = {
  id: string;
  legal_name: string;
  registry_status: string | null;
  trading_name: string | null;
  country: string;
  region: string | null;
  city: string | null;
  website: string | null;
  sector: string | null;
  industry_codes: string[];
  estimated_employee_min: number | null;
  estimated_employee_max: number | null;
  ownership_type: string;
  qualification_status: string;
  review_status: string;
  first_seen_at: string;
  last_verified_at: string | null;
  freshness: string;
  source_ids: string[];
  source_count: number;
  multi_source_fields: number;
  conflict_fields: number;
  completeness: number;
  headcount_status: string;
  registry_id: string | null;
  open_positions: number | null;
  founder_signal: boolean;
  family_business_signal: boolean;
  website_enriched: boolean;
};

export type CompanyPage = {
  items: CompanySummary[];
  total: number;
  page: number;
  page_size: number;
  filters: Record<string, unknown>;
};

export type SellerProspect = {
  company_id: string;
  legal_name: string;
  registry_id: string | null;
  registry_status: string | null;
  sector: string | null;
  peer_group: string | null;
  focus_band: string;
  quality_band: string;
  evidence_status: string;
  next_action: string;
  latest_year: number | null;
  latest_revenue_eur: number | null;
  latest_operating_margin: number | null;
  three_year_median_margin: number | null;
  three_year_revenue_cagr: number | null;
  stable_revenue: boolean | null;
  positive_profit_years: number | null;
  latest_equity_ratio: number | null;
  peer_count: number | null;
  margin_peer_z: number | null;
  equity_peer_z: number | null;
  financial_profile_index: number | null;
  buyer_fit: "not_assessed";
  owner_intent: "unknown";
  latest_employees_fte: number | null;
  consolidated_revenue_eur: number | null;
  flags: ("group_parent" | "holding_activity")[];
  registry_url: string | null;
  review_reasons: string[];
  open_questions: string[];
  filing_ids: string[];
  source_urls: string[];
  issues: string[];
};

export type FunnelStage = {
  key: string;
  label: string;
  count: number;
  rule: string;
};

export type PeerGroup = {
  group: string;
  peer_count: number;
  median_margin: number;
  median_equity_ratio: number;
};

export type SellerFunnel = {
  total_companies: number;
  core_size: number;
  three_year_profitable: number;
  advisor_review: number;
  stages: FunnelStage[];
  peer_groups: PeerGroup[];
  items: SellerProspect[];
  methodology: string;
};

export type Fact = {
  id: string;
  field_name: string;
  value_json: unknown;
  original_value: string | null;
  source_id: string;
  source_name: string | null;
  source_key: string | null;
  source_url: string | null;
  ingestion_run_id: string | null;
  snapshot_id: string | null;
  observed_at: string;
  valid_from: string;
  valid_to: string | null;
  base_confidence: string;
  confidence: string;
  usage_policy: string;
  review_status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  is_correction: boolean;
  corrects_fact_id: string | null;
  correction_reason: string | null;
};

export type FieldView = {
  field_name: string;
  value: unknown;
  status: string;
  label: string;
  source_ids: string[];
  supporting_fact_ids: string[];
  conflicting_values: unknown[];
};

export type Contact = {
  id: string;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  profile_url: string | null;
  country: string | null;
  source_id: string;
  source_url: string | null;
  confidence: string;
  contact_basis: string;
  usage_policy: string;
  last_verified_at: string | null;
};

export type Duplicate = {
  id: string;
  company_a_id: string;
  company_b_id: string;
  company_a_name: string | null;
  company_b_name: string | null;
  score: number;
  band: string;
  reasons: { signal: string; effect: string; detail: string }[];
  status: string;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_reason: string | null;
  created_at: string;
};

export type AuditEvent = {
  id: string;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  company_id: string | null;
  details: Record<string, unknown>;
};

export type TimelineEntry = {
  at: string;
  kind: string;
  title: string;
  source_id: string | null;
  detail: Record<string, unknown>;
};

export type CompanyDetail = {
  company: CompanySummary;
  description: string | null;
  revenue: { min: number; max: number; currency: string } | null;
  merged_into_id: string | null;
  fields: FieldView[];
  facts: Fact[];
  history: Fact[];
  identifiers: { kind: string; value: string; source_id: string | null; derived: boolean }[];
  financials: Financial[];
  registered_address: RegisteredAddress | null;
  contacts: Contact[];
  duplicates: Duplicate[];
  audit_events: AuditEvent[];
  timeline: TimelineEntry[];
  warnings: string[];
};

export type Financial = {
  id: string;
  period_start: string | null;
  period_end: string | null;
  fiscal_year: number;
  currency: string | null;
  revenue: number | null;
  ebitda: number | null;
  net_income: number | null;
  dividends: number | null;
  capex: number | null;
  depreciation: number | null;
  depreciation_and_impairment: number | null;
  operating_profit: number | null;
  profit_before_tax: number | null;
  total_assets: number | null;
  equity: number | null;
  labour_cost: number | null;
  employees_fte: number | null;
  source_id: string;
  source_name: string | null;
  source_url: string;
  source_file: string | null;
  snapshot_id: string | null;
  observed_at: string;
  confidence: string;
  usage_policy: string;
  parser_version: string;
  statement_scope: string | null;
  value_type: string;
  filing_id: string;
  document_id: string | null;
  unit: string | null;
  restated: boolean | null;
  calculation_formula: string | null;
  ingestion_run_id: string | null;
  review_status: string;
  registry_code: string;
  source_key: string;
  content_hash: string;
  source_values: Record<string, unknown>[];
};

export type RegisteredAddress = {
  id: string;
  address_line: string | null;
  postal_code: string | null;
  city: string | null;
  municipality: string | null;
  county: string | null;
  ehak_code: string | null;
  country: string | null;
  source_id: string;
  source_name: string | null;
  source_url: string;
  source_file: string | null;
  snapshot_id: string | null;
  ingestion_run_id: string | null;
  observed_at: string;
  parser_version: string;
  content_hash: string;
  warnings: string[];
  valid_from: string;
  valid_to: string | null;
};

export type QualityReport = {
  generated_at: string;
  totals: Record<string, number | Record<string, number>>;
  missing_fields: Record<string, { label: string; count: number; companies: { company_id: string; legal_name: string }[] }>;
  stale_records: { company_id: string; legal_name: string; last_verified_at: string | null }[];
  conflicts: {
    company_id: string;
    legal_name: string;
    field_name: string;
    values: { fact_id: string; value: unknown; source_id: string; observed_at: string }[];
    resolved_by_correction: boolean;
  }[];
  validation_failures: {
    run_id: string;
    source_id: string;
    row: number;
    source_key: string | null;
    errors: string[];
    run_started_at: string;
  }[];
  top_warnings: { message: string; count: number }[];
};
