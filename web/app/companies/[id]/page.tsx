import Link from "next/link";

import { Badge } from "@/components/Badge";
import { apiGet, fmtDate, fmtEmployees, fmtValue } from "@/lib/api";
import type { CompanyDetail, Fact } from "@/lib/types";

import { CorrectionForm, EraseContactButton, ReviewControls } from "./ClientControls";

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

export default async function CompanyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const d = await apiGet<CompanyDetail>(`/companies/${id}`);
  const c = d.company;
  const conflicts = d.fields.filter((f) => f.status === "conflicting");

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
