import Link from "next/link";

import { Badge } from "@/components/Badge";
import { apiGet, fmtDate, fmtEmployees, fmtMoney, fmtValue } from "@/lib/api";
import type { CompanyDetail, DigitalDecayView, Fact } from "@/lib/types";

import { CorrectionForm, EraseContactButton, ReviewControls, RunDecayCheck } from "./ClientControls";

export const dynamic = "force-dynamic";

function FactRow({ f }: { f: Fact }) {
  return (
    <tr className={f.valid_to ? "row-muted" : ""}>
      <td className="mono small">{f.field_name}</td>
      <td>
        {fmtValue(f.value_json)}
        {f.original_value && f.original_value !== fmtValue(f.value_json) && (
          <div className="small muted">source value: “{f.original_value}”</div>
        )}
        {f.code_system && (
          <div className="small muted">
            {f.code_system} {f.code_version ?? "version unknown"}
          </div>
        )}
        {f.correction_reason && <div className="small">reason: {f.correction_reason}</div>}
      </td>
      <td className="small">
        {f.source_name ?? f.source_id}
        <div className="muted mono">{f.source_id}</div>
      </td>
      <td className="small">
        {f.source_url ? (
          <a href={f.source_url} target="_blank" rel="noreferrer">
            {f.source_key ?? "link"}
          </a>
        ) : (
          <span className="mono">{f.source_key ?? "—"}</span>
        )}
        {f.ingestion_run_id && (
          <div>
            <Link href={`/runs/${f.ingestion_run_id}`} className="muted">
              run {f.ingestion_run_id.slice(0, 8)}
            </Link>
          </div>
        )}
      </td>
      <td className="small">{fmtDate(f.observed_at)}</td>
      <td>
        <Badge value={f.confidence} />
      </td>
      <td className="small">
        <Badge value={f.review_status === "corrected" ? "manually-corrected" : f.review_status} label={f.review_status} />
        <div className="muted">{f.usage_policy}</div>
        {f.valid_to && <div className="muted">superseded {fmtDate(f.valid_to)}</div>}
      </td>
    </tr>
  );
}

function EvidenceLink({ url, label }: { url: string | null; label?: string }) {
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className="small">
      {label ?? "evidence"}
    </a>
  ) : (
    <span className="small muted">no evidence URL</span>
  );
}

function DecayResult({ view }: { view: DigitalDecayView }) {
  const s = view.signal;
  return (
    <dl className="kv">
      <dt>Verdict</dt>
      <dd>
        <Badge value={s.verdict} />{" "}
        <span className="small muted">
          {s.stale_count} of {s.determinable_count} determinable checks stale
        </span>
      </dd>
      <dt>Website</dt>
      <dd>
        {s.domain ? (
          <a href={`https://${s.domain}`} target="_blank" rel="noreferrer">
            {s.domain}
          </a>
        ) : (
          "not found"
        )}{" "}
        <Badge
          value={s.domain_verification === "unverified" ? "unknown" : "verified"}
          label={s.domain_verification.replaceAll("_", " ")}
        />
      </dd>
      <dt>Footer copyright</dt>
      <dd>
        <Badge value={s.checks.copyright.state} /> {s.checks.copyright.year ?? "—"}
        {s.checks.copyright.age_years !== null && (
          <span className="small muted"> ({s.checks.copyright.age_years} y old)</span>
        )}{" "}
        <EvidenceLink url={s.checks.copyright.evidence_url} />
      </dd>
      <dt>Latest news / press</dt>
      <dd>
        <Badge value={s.checks.news.state} /> {s.checks.news.latest_date ?? "—"}
        {s.checks.news.age_months !== null && (
          <span className="small muted"> ({s.checks.news.age_months} months ago)</span>
        )}
        {s.checks.news.method && <span className="small muted mono"> via {s.checks.news.method}</span>}{" "}
        <EvidenceLink url={s.checks.news.evidence_url} />
        {s.checks.news.posts_18m != null && (
          <div className="small">
            {s.checks.news.posts_18m} post{s.checks.news.posts_18m === 1 ? "" : "s"} in 18 months
            {s.checks.news.cadence_source && (
              <span className="muted">
                {" "}
                (from {s.checks.news.cadence_source === "sitemap" ? "sitemap" : "news page"})
              </span>
            )}
          </div>
        )}
        {s.checks.news.reason && (
          <div className="small muted">
            stale because:{" "}
            {s.checks.news.reason === "low_cadence"
              ? "fewer than 3 posts in 18 months"
              : "no post in 18 months"}
          </div>
        )}
        {s.checks.news.post_dates && s.checks.news.post_dates.length > 0 && (
          <div className="small muted mono">{s.checks.news.post_dates.join(" · ")}</div>
        )}
      </dd>
      <dt>Hiring</dt>
      <dd>
        <Badge value={s.checks.hiring.state} />{" "}
        {s.checks.hiring.open_roles !== null ? `${s.checks.hiring.open_roles} open roles` : "open roles unknown"}
        {s.checks.hiring.ats && <span className="small muted"> · ATS {s.checks.hiring.ats}</span>}{" "}
        <EvidenceLink url={s.checks.hiring.careers_url} label="careers page" />
      </dd>
      <dt>Headcount (register FTE)</dt>
      <dd>
        {s.checks.headcount ? (
          <>
            <Badge value={s.checks.headcount.state} />{" "}
            {s.checks.headcount.change_pct !== null && (
              <>
                {s.checks.headcount.change_pct > 0 ? "+" : ""}
                {s.checks.headcount.change_pct.toFixed(1)}%{" "}
                <span className="small muted">
                  FY{s.checks.headcount.from_year}→FY{s.checks.headcount.to_year}
                </span>
              </>
            )}
            {s.checks.headcount.series.length > 0 && (
              <div className="small muted">
                {s.checks.headcount.series.map(([year, fte]) => `${year}: ${fte}`).join(" · ")}
              </div>
            )}
          </>
        ) : (
          <span className="muted">no register FTE filed</span>
        )}
      </dd>
      <dt>Last-Modified header</dt>
      <dd className="small">
        {s.checks.last_modified.header ? fmtDate(s.checks.last_modified.header) : "not sent"}
      </dd>
      <dt>Revenue used</dt>
      <dd>
        {s.revenue ? (
          <>
            {fmtMoney(s.revenue.amount, s.revenue.currency)}{" "}
            <span className="small muted">
              FY{s.revenue.fiscal_year} · {s.revenue.value_type}
            </span>
          </>
        ) : (
          <span className="muted">no reported revenue — cannot be classed as coasting</span>
        )}
      </dd>
      <dt>Provenance</dt>
      <dd className="small">
        <span className="mono">{view.source_id}</span> · observed {fmtDate(view.observed_at, true)} ·{" "}
        <Badge value={view.confidence} /> <Badge value={view.review_status} />
        {view.ingestion_run_id && (
          <>
            {" "}
            · <Link href={`/runs/${view.ingestion_run_id}`}>run {view.ingestion_run_id.slice(0, 8)}</Link>
          </>
        )}
        <div className="muted">parser {s.version}</div>
      </dd>
      {s.warnings.length > 0 && (
        <>
          <dt>Warnings</dt>
          <dd>
            <ul style={{ margin: 0 }}>
              {s.warnings.map((w) => (
                <li key={w} className="small">
                  {w}
                </li>
              ))}
            </ul>
          </dd>
        </>
      )}
    </dl>
  );
}

function DecayPanel({ companyId, view }: { companyId: string; view: DigitalDecayView | null | undefined }) {
  return (
    <section className="panel">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h2>Digital decay (operational stagnation)</h2>
        <RunDecayCheck companyId={companyId} hasResult={!!view?.signal} />
      </div>
      <p className="small muted" style={{ marginTop: -6 }}>
        Opt-in website signal (source <span className="mono">web-digital-decay</span>). Footer copyright ≥2 years old,
        news publishing cadence (no post for ≥18 months, or fewer than 3 posts in 18 months — translations counted
        once, bulk re-save dates ignored) and zero open roles on the careers page, read against reported revenue.
        Verdict <em>watch</em>: website otherwise maintained, but zero open roles and flat or shrinking register
        headcount (FTE) at ≥€5M revenue. Values are estimated heuristics from the company&apos;s own website; register
        FTE is a filed figure. Unknown checks never count as stale. LinkedIn is not used.
      </p>
      {view?.signal ? (
        <DecayResult view={view} />
      ) : (
        <p className="muted">No website check has been run for this company.</p>
      )}
    </section>
  );
}

export default async function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const d = await apiGet<CompanyDetail>(`/companies/${id}`);
  const c = d.company;
  const conflicts = d.fields.filter((f) => f.status === "conflicting");
  const shareCapital = d.fields.find((f) => f.field_name === "share_capital") ?? null;

  return (
    <>
      <div className="page-head">
        <div>
          <p className="small">
            <Link href="/companies">← Company database</Link>
          </p>
          <h1>{c.legal_name}</h1>
          <p className="subtitle">
            {c.country} · {c.city ?? "city unknown"} · {c.sector ?? "sector unknown"} ·{" "}
            {fmtEmployees(c.estimated_employee_min, c.estimated_employee_max)} employees{" "}
            · Registry status: <strong>{c.registry_status ?? "unknown"}</strong>{" "}
            <Badge value={c.qualification_status} /> <Badge value={c.headcount_status} />{" "}
            <Badge value={c.freshness} /> <Badge value={c.review_status} />
          </p>
        </div>
        <ReviewControls companyId={c.id} status={c.review_status} />
      </div>

      {d.merged_into_id && (
        <div className="notice notice-info">
          This record was merged into <Link href={`/companies/${d.merged_into_id}`}>the surviving company</Link>.
        </div>
      )}
      {d.warnings.length > 0 && (
        <div className="notice">
          <strong>Data-quality warnings</strong>
          <ul style={{ margin: "4px 0 0" }}>
            {d.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-2">
        <section className="panel">
          <h2>Registered address</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            This is the official registered seat from the Estonian register, not an inferred operating location.
          </p>
          {d.registered_address ? (
            <dl className="kv">
              <dt>Address</dt>
              <dd>{d.registered_address.address_line ?? "—"}</dd>
              <dt>Postal code</dt>
              <dd>{d.registered_address.postal_code ?? "—"}</dd>
              <dt>City</dt>
              <dd>{d.registered_address.city ?? "—"}</dd>
              <dt>Municipality</dt>
              <dd>{d.registered_address.municipality ?? "—"}</dd>
              <dt>County</dt>
              <dd>{d.registered_address.county ?? "—"}</dd>
              <dt>EHAK code</dt>
              <dd className="mono">{d.registered_address.ehak_code ?? "—"}</dd>
              <dt>Country</dt>
              <dd>{d.registered_address.country ?? "—"}</dd>
              <dt>Provenance</dt>
              <dd>
                <a href={d.registered_address.source_url} target="_blank" rel="noreferrer">
                  {d.registered_address.source_file ?? d.registered_address.source_id}
                </a>
                <div className="small muted">observed {fmtDate(d.registered_address.observed_at)}</div>
              </dd>
              {d.registered_address.warnings.length > 0 && (
                <>
                  <dt>Source warnings</dt>
                  <dd>{d.registered_address.warnings.join("; ")}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="muted">No registered address was supplied.</p>
          )}
        </section>

        <section className="panel">
          <h2>Financials by year</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            Amounts are formatted in EUR. Reported values keep their source signs and provenance. Reported EBITDA
            takes precedence; otherwise EBITDA = operating profit − the signed depreciation/impairment line (adding
            back a negative expense). Values are not annualized.
          </p>
          <div className="table-wrap">
            <table className="financial-table">
              <thead>
                <tr>
                  <th>Year</th>
                  <th>Period / scope</th>
                  <th className="num">Revenue</th>
                  <th className="num">Net income</th>
                  <th className="num">Operating profit</th>
                  <th className="num">D&amp;A (reported sign)</th>
                  <th className="num">EBITDA</th>
                  <th>Provenance</th>
                </tr>
              </thead>
              <tbody>
                {d.financials.map((f) => (
                  <tr key={f.id}>
                    <td>{f.fiscal_year}</td>
                    <td className="small">
                      {f.period_start ?? "—"} – {f.period_end ?? "—"}
                      <div className="muted">
                        {f.period_days === null
                          ? "period length unknown"
                          : `${f.period_days} days · ${
                              f.period_length_class === "standard_12_month"
                                ? "standard 12-month"
                                : f.period_length_class ?? "unclassified"
                            }`}
                      </div>
                      <div className="muted">{f.statement_scope ?? "scope unknown"}</div>
                    </td>
                    <td className="num">{fmtMoney(f.revenue, f.currency ?? "EUR")}</td>
                    <td className="num">{fmtMoney(f.net_income, f.currency ?? "EUR")}</td>
                    <td className="num">{fmtMoney(f.operating_profit, f.currency ?? "EUR")}</td>
                    <td className="num">{fmtMoney(f.depreciation_and_impairment, f.currency ?? "EUR")}</td>
                    <td className="num">
                      <strong>{fmtMoney(f.ebitda, f.currency ?? "EUR")}</strong>
                      <div className="small muted">
                        {f.value_type === "derived" ? "Derived" : "Reported"}
                        {f.value_type === "derived" && f.operating_profit !== null &&
                          f.depreciation_and_impairment !== null && (
                            <div>
                              {fmtMoney(f.operating_profit, f.currency ?? "EUR")} − (
                              {fmtMoney(f.depreciation_and_impairment, f.currency ?? "EUR")})
                            </div>
                          )}
                      </div>
                    </td>
                    <td className="small financial-source">
                      <a href={f.source_url} target="_blank" rel="noreferrer">
                        {f.source_file ?? f.source_id}
                      </a>
                      <div className="muted mono">{f.source_key}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.financials.length === 0 && <p className="muted">No annual financial lines were imported.</p>}
        </section>
      </div>

      <section className="panel">
        <h2>Ownership</h2>
        <p className="small muted" style={{ marginTop: -6 }}>
          Current share capital and shareholders (osanikud) from the official register. A person shareholder
          is shown by name and holding only — the register&apos;s national ID code is never stored.
        </p>
        <dl className="kv">
          <dt>Share capital</dt>
          <dd>
            {shareCapital && shareCapital.value !== null ? (
              <>
                {fmtValue(shareCapital.value)} <Badge value={shareCapital.label} />
              </>
            ) : (
              <span className="muted">unknown</span>
            )}
          </dd>
        </dl>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Holder</th>
                <th>Type</th>
                <th className="num">Holding</th>
                <th className="num">%</th>
                <th>Since</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {d.shareholders.map((s) => (
                <tr key={s.id}>
                  <td className={s.holder_type === "person" ? "pii" : undefined}>
                    {s.holder_name}
                    {s.holder_registry_code && (
                      <div className="small muted mono">
                        {s.holder_registry_code}
                        {s.holder_country ? ` (${s.holder_country})` : ""}
                      </div>
                    )}
                    {s.role && <div className="small muted">{s.role}</div>}
                  </td>
                  <td className="small">
                    {s.holder_type === "person" ? "Person" : s.holder_type === "legal_entity" ? "Company" : "Unknown"}
                    {s.holding_type && <div className="muted">{s.holding_type}</div>}
                  </td>
                  <td className="num">{fmtMoney(s.holding_amount, s.holding_currency ?? "EUR")}</td>
                  <td className="num">{s.holding_percent === null ? "—" : `${s.holding_percent}%`}</td>
                  <td className="small">{fmtDate(s.effective_from)}</td>
                  <td className="small">
                    <a href={s.source_url} target="_blank" rel="noreferrer">
                      {s.source_file ?? s.source_id}
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {d.shareholders.length === 0 && <p className="muted">No current shareholders were supplied.</p>}
      </section>

      <DecayPanel companyId={c.id} view={d.digital_decay} />

      <div className="grid grid-2">
        <section className="panel">
          <h2>Consolidated profile</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            Each value is resolved from source facts; the label says how well it is supported.
          </p>
          <table>
            <tbody>
              {d.fields.map((f) => (
                <tr key={f.field_name}>
                  <td className="mono small muted">{f.field_name}</td>
                  <td>
                    {f.value === null ? <span className="muted">unknown</span> : fmtValue(f.value)}
                    {f.conflicting_values.length > 0 && (
                      <div className="small error">
                        also claimed: {f.conflicting_values.map((v) => fmtValue(v)).join(" · ")}
                      </div>
                    )}
                  </td>
                  <td>
                    <Badge value={f.label} />
                  </td>
                  <td className="small muted">{f.source_ids.join(", ")}</td>
                </tr>
              ))}
              {d.revenue === null && (
                <tr>
                  <td colSpan={4} className="small muted">
                    No verified financials: no valuation or revenue claim is made.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <h2>Identity keys</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            Stable keys used for entity resolution. Derived keys (e.g. VAT from a Finnish Y-tunnus) are matching keys
            only, never displayed as facts.
          </p>
          <table>
            <tbody>
              {d.identifiers.map((i) => (
                <tr key={`${i.kind}:${i.value}`}>
                  <td className="small muted">{i.kind}</td>
                  <td className="mono small">{i.value}</td>
                  <td className="small muted">
                    {i.source_id}
                    {i.derived && " (derived)"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {d.duplicates.length > 0 && (
            <>
              <h3>Duplicate candidates</h3>
              {d.duplicates.map((dup) => {
                const otherId = dup.company_a_id === c.id ? dup.company_b_id : dup.company_a_id;
                const otherName = dup.company_a_id === c.id ? dup.company_b_name : dup.company_a_name;
                return (
                  <div key={dup.id} className="small" style={{ marginBottom: 8 }}>
                    <Badge value={dup.status} /> <Badge value={dup.band} label={`${dup.band} ${dup.score}`} />{" "}
                    <Link href={`/companies/${otherId}`}>{otherName}</Link>
                    <ul style={{ margin: "2px 0" }}>
                      {dup.reasons.map((r, i) => (
                        <li key={i} className={`reason-${r.effect === "for" ? "for" : r.effect === "against" ? "against" : "neutral"}`}>
                          {r.detail}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
              <Link href="/quality#duplicates" className="small">
                Review in data quality →
              </Link>
            </>
          )}
        </section>
      </div>

      {conflicts.length > 0 && (
        <section className="panel">
          <h2>Conflicts</h2>
          {conflicts.map((f) => (
            <div key={f.field_name} style={{ marginBottom: 10 }}>
              <strong className="mono">{f.field_name}</strong>
              <table>
                <tbody>
                  {d.facts
                    .filter((x) => x.field_name === f.field_name && !x.is_correction)
                    .map((x) => (
                      <tr key={x.id}>
                        <td>{fmtValue(x.value_json)}</td>
                        <td className="small">{x.source_name ?? x.source_id}</td>
                        <td className="small">{fmtDate(x.observed_at)}</td>
                        <td>
                          <Badge value={x.confidence} />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ))}
          <p className="small muted">Resolve by adding a correction below. Source facts are never overwritten.</p>
        </section>
      )}

      <section className="panel">
        <h2>Source-backed facts</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Field</th>
                <th>Value</th>
                <th>Source</th>
                <th>Reference</th>
                <th>Observed</th>
                <th>Confidence</th>
                <th>Review / policy</th>
              </tr>
            </thead>
            <tbody>
              {d.facts.map((f) => (
                <FactRow key={f.id} f={f} />
              ))}
            </tbody>
          </table>
        </div>
        {d.history.length > 0 && (
          <details>
            <summary>Superseded versions ({d.history.length})</summary>
            <table>
              <tbody>
                {d.history.map((f) => (
                  <FactRow key={f.id} f={f} />
                ))}
              </tbody>
            </table>
          </details>
        )}
      </section>

      <div className="grid grid-2">
        <section className="panel">
          <h2>Manual correction</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            Adds a <Badge value="manually-corrected" /> fact. The original facts, their values and provenance stay
            intact and visible.
          </p>
          <CorrectionForm companyId={c.id} facts={d.facts} />
        </section>

        <section className="panel pii">
          <h2>Contacts (personal data)</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            Shown only as supplied by an approved source. Emails and phone numbers are never guessed or enriched.
          </p>
          {d.contacts.length === 0 && <p className="muted">No source-backed contacts.</p>}
          {d.contacts.map((p) => (
            <div key={p.id} style={{ marginBottom: 12 }}>
              <strong>{p.name}</strong> {p.role && <span className="muted">· {p.role}</span>}
              <div className="small">
                {p.email ?? <span className="muted">no email supplied</span>} ·{" "}
                {p.phone ?? <span className="muted">no phone supplied</span>}
              </div>
              <div className="small muted">
                {p.source_id} · basis {p.contact_basis} · {p.usage_policy} · verified {fmtDate(p.last_verified_at)}
              </div>
              <EraseContactButton contactId={p.id} />
            </div>
          ))}
        </section>
      </div>

      <div className="grid grid-2">
        <section className="panel">
          <h2>Evidence timeline</h2>
          <ul className="timeline">
            {d.timeline.slice(0, 60).map((t, i) => (
              <li key={i} className={`k-${t.kind}`}>
                <span className="small muted">{fmtDate(t.at, true)}</span> <strong className="small">{t.title}</strong>
                {t.source_id && <span className="small muted"> · {t.source_id}</span>}
                {t.kind !== "audit" && "value" in t.detail && (
                  <div className="small">
                    {fmtValue(t.detail.value)} <Badge value={String(t.detail.confidence)} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          <h2>Audit history</h2>
          {d.audit_events.length === 0 && <p className="muted">No manual actions yet.</p>}
          <table>
            <tbody>
              {d.audit_events.map((a) => (
                <tr key={a.id}>
                  <td className="small">{fmtDate(a.occurred_at, true)}</td>
                  <td className="small mono">{a.action}</td>
                  <td className="small">{a.actor}</td>
                  <td className="small muted mono" style={{ maxWidth: 320, wordBreak: "break-word" }}>
                    {JSON.stringify(a.details)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}
