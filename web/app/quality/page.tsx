import Link from "next/link";

import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { apiGet, fmtDate, fmtValue } from "@/lib/api";
import type { Duplicate, QualityReport } from "@/lib/types";

export const dynamic = "force-dynamic";

function Stat({ n, label, tone }: { n: number | string; label: string; tone?: string }) {
  return (
    <div className="stat">
      <div className={`n ${tone && Number(n) > 0 ? `count-${tone}` : ""}`}>{n}</div>
      <div className="l">{label}</div>
    </div>
  );
}

export default async function QualityPage() {
  const [report, dupes, resolved] = await Promise.all([
    apiGet<QualityReport>("/quality/report"),
    apiGet<Duplicate[]>("/quality/duplicates", { status: "open" }),
    apiGet<Duplicate[]>("/quality/duplicates", { status: "all" }),
  ]);
  const t = report.totals as Record<string, number>;
  const qual = report.totals.qualification as Record<string, number>;
  const done = resolved.filter((d) => d.status !== "open");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Data quality</h1>
          <p className="subtitle">Generated {fmtDate(report.generated_at, true)} from current facts.</p>
        </div>
        <span className="action">
          <ActionButton path="/quality/duplicates/detect" label="Re-scan duplicates" />
          <ActionButton
            path="/retention/expire"
            label="Expire raw snapshots now"
            confirm="Drop raw payloads of snapshots past their source's retention period? Hashes and facts are kept."
          />
        </span>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <Stat n={t.companies} label="companies" />
        <Stat n={qual.qualified ?? 0} label="≥20 employees (qualified)" />
        <Stat n={qual.sub_scale ?? 0} label="sub-scale (1–2)" tone="warn" />
        <Stat n={qual.unknown_headcount ?? 0} label="headcount unknown" tone="warn" />
        <Stat n={`${t.average_completeness}%`} label="avg completeness" />
        <Stat n={t.open_duplicate_candidates} label="open duplicate candidates" tone="warn" />
        <Stat n={t.unresolved_conflicts} label="unresolved conflicts" tone="bad" />
        <Stat n={t.stale_records} label="stale records" tone="warn" />
        <Stat n={t.validation_failures} label="validation failures" tone="bad" />
        <Stat n={t.expired_snapshots} label="expired snapshots" />
        <Stat n={t.snapshots_due_for_expiry} label="snapshots due for expiry" tone="warn" />
      </div>

      <section className="panel" id="duplicates">
        <h2>Duplicate candidates ({dupes.length} open)</h2>
        <p className="small muted" style={{ marginTop: -6 }}>
          Exact stable keys (registry ID, VAT, domain) are resolved automatically at ingestion. Everything below is only
          a <em>possible</em> match and needs a reviewer: merge (same legal entity), link (related, e.g. same group) or
          dismiss (different companies).
        </p>
        {dupes.length === 0 && <p className="muted">No open candidates.</p>}
        {dupes.map((d) => (
          <div key={d.id} className="panel" style={{ marginBottom: 10 }}>
            <div className="page-head" style={{ marginBottom: 4 }}>
              <div>
                <Badge value={d.band} label={`${d.band} · score ${d.score}`} />{" "}
                <Link href={`/companies/${d.company_a_id}`}>{d.company_a_name}</Link> ↔{" "}
                <Link href={`/companies/${d.company_b_id}`}>{d.company_b_name}</Link>
              </div>
              <span className="action">
                {!d.reasons.some((r) => r.signal === "registry_id" && r.effect === "against") && (
                  <>
                    <ActionButton
                      path={`/quality/duplicates/${d.id}/merge`}
                      body={{ survivor_id: d.company_a_id }}
                      label={`Merge into “${d.company_a_name}”`}
                      promptReason="Why are these the same legal entity?"
                      variant="primary"
                    />
                    <ActionButton
                      path={`/quality/duplicates/${d.id}/merge`}
                      body={{ survivor_id: d.company_b_id }}
                      label={`Merge into “${d.company_b_name}”`}
                      promptReason="Why are these the same legal entity?"
                    />
                  </>
                )}
                <ActionButton
                  path={`/quality/duplicates/${d.id}/link`}
                  label="Link as related"
                  promptReason="How are they related (e.g. group subsidiary)?"
                />
                <ActionButton
                  path={`/quality/duplicates/${d.id}/dismiss`}
                  label="Not a duplicate"
                  promptReason="Why are these different companies?"
                  variant="danger"
                />
              </span>
            </div>
            <ul className="small" style={{ margin: 0 }}>
              {d.reasons.map((r, i) => (
                <li
                  key={i}
                  className={`reason-${r.effect === "for" ? "for" : r.effect === "against" ? "against" : "neutral"}`}
                >
                  <strong>{r.signal}</strong> ({r.effect}): {r.detail}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {done.length > 0 && (
          <details>
            <summary>Resolved ({done.length})</summary>
            <table>
              <tbody>
                {done.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <Badge value={d.status} />
                    </td>
                    <td className="small">
                      {d.company_a_name} ↔ {d.company_b_name}
                    </td>
                    <td className="small muted">
                      {d.resolved_by} · {fmtDate(d.resolved_at)} · {d.resolution_reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </section>

      <div className="grid grid-2">
        <section className="panel">
          <h2>Unresolved conflicts</h2>
          {report.conflicts.length === 0 && <p className="muted">None.</p>}
          {report.conflicts.map((cf) => (
            <div key={`${cf.company_id}-${cf.field_name}`} style={{ marginBottom: 8 }}>
              <Link href={`/companies/${cf.company_id}`}>{cf.legal_name}</Link> · <code>{cf.field_name}</code>{" "}
              {cf.resolved_by_correction && <Badge value="manually-corrected" />}
              <ul className="small" style={{ margin: "2px 0" }}>
                {cf.values.map((v) => (
                  <li key={v.fact_id}>
                    {fmtValue(v.value)} <span className="muted">— {v.source_id}, {fmtDate(v.observed_at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        <section className="panel">
          <h2>Missing fields</h2>
          <table>
            <tbody>
              {Object.entries(report.missing_fields).map(([k, v]) => (
                <tr key={k}>
                  <td>{v.label}</td>
                  <td className="num">{v.count}</td>
                  <td className="small">
                    {v.companies.slice(0, 6).map((c) => (
                      <span key={c.company_id}>
                        <Link href={`/companies/${c.company_id}`}>{c.legal_name}</Link>{" "}
                      </span>
                    ))}
                    {v.count > 6 && <span className="muted">+{v.count - 6} more</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">Missing values reduce completeness; they are never filled by guessing.</p>
        </section>
      </div>

      <div className="grid grid-2">
        <section className="panel">
          <h2>Stale records</h2>
          {report.stale_records.length === 0 && <p className="muted">None.</p>}
          <table>
            <tbody>
              {report.stale_records.map((s) => (
                <tr key={s.company_id}>
                  <td>
                    <Link href={`/companies/${s.company_id}`}>{s.legal_name}</Link>
                  </td>
                  <td className="small muted">last verified {fmtDate(s.last_verified_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel">
          <h2>Validation failures</h2>
          {report.validation_failures.length === 0 && <p className="muted">None.</p>}
          <table>
            <tbody>
              {report.validation_failures.slice(0, 30).map((v, i) => (
                <tr key={i}>
                  <td className="small">
                    <Link href={`/runs/${v.run_id}`}>
                      {v.source_id} row {v.row}
                    </Link>
                  </td>
                  <td className="small error">{v.errors.join("; ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Most frequent warnings</h3>
          <table>
            <tbody>
              {report.top_warnings.map((w) => (
                <tr key={w.message}>
                  <td className="small">{w.message}</td>
                  <td className="num">{w.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </>
  );
}
