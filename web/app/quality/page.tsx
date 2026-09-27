import Link from "next/link";

import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader, Panel, Stat, type Tone } from "@/components/ui";
import { apiGet, fmtDate, fmtValue } from "@/lib/api";
import type { Duplicate, QualityReport } from "@/lib/types";

import { DuplicateActions } from "./DuplicateActions";
import { effectKind, fmtCount, humanise } from "./format";
import styles from "./quality.module.css";

export const dynamic = "force-dynamic";

const VALIDATION_LIMIT = 30;

/** Tone only applies when the figure is above zero, so an all-clear total stays neutral. */
function toned(n: number | undefined, tone: Tone): Tone | undefined {
  return typeof n === "number" && n > 0 ? tone : undefined;
}

export default async function QualityPage() {
  const [report, dupes, resolved] = await Promise.all([
    apiGet<QualityReport>("/quality/report"),
    apiGet<Duplicate[]>("/quality/duplicates", { status: "open" }),
    apiGet<Duplicate[]>("/quality/duplicates", { status: "all" }),
  ]);
  const t = report.totals as Record<string, number>;
  const qual = (report.totals.qualification ?? {}) as Record<string, number>;
  const done = resolved.filter((d) => d.status !== "open");
  const missing = Object.entries(report.missing_fields).sort(([, a], [, b]) => b.count - a.count);
  const staleTotal = t.stale_records ?? report.stale_records.length;
  const failures = report.validation_failures;

  const stats: { n: number | undefined; label: string; tone?: Tone; pct?: boolean }[] = [
    { n: t.companies, label: "companies" },
    { n: qual.qualified ?? 0, label: "≥20 employees (qualified)", tone: "good" },
    { n: qual.sub_scale ?? 0, label: "sub-scale (1–2)", tone: "warn" },
    { n: qual.unknown_headcount ?? 0, label: "headcount unknown", tone: "warn" },
    { n: t.average_completeness, label: "avg completeness", tone: "accent", pct: true },
    { n: t.open_duplicate_candidates, label: "open duplicate candidates", tone: "warn" },
    { n: t.unresolved_conflicts, label: "unresolved conflicts", tone: "bad" },
    { n: t.stale_records, label: "stale records", tone: "warn" },
    { n: t.validation_failures, label: "validation failures", tone: "bad" },
    { n: t.expired_snapshots, label: "expired snapshots" },
    { n: t.snapshots_due_for_expiry, label: "snapshots due for expiry", tone: "warn" },
  ];

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Data governance" }, { label: "Quality review" }]}
        title="Data quality"
        subtitle={
          <>
            Current quality signals across the company database. Missing fields lower completeness; they are never
            filled by guessing. Generated {fmtDate(report.generated_at, true)} from current facts.
          </>
        }
        actions={
          <span className="action">
            <ActionButton path="/quality/duplicates/detect" label="Re-scan duplicates" />
            <ActionButton
              path="/retention/expire"
              label="Expire raw snapshots now"
              confirm="Drop raw payloads of snapshots past their source's retention period? Hashes and facts are kept."
            />
          </span>
        }
      />

      {[stats.slice(0, 5), stats.slice(5)].map((row, i) => (
        <div key={i} className={`stats ${styles.statsRow}`}>
          {row.map((s) => (
            <Stat
              key={s.label}
              value={s.pct ? (typeof s.n === "number" ? `${s.n}%` : "—") : fmtCount(s.n)}
              label={s.label}
              tone={s.tone ? toned(s.n, s.tone) : undefined}
            />
          ))}
        </div>
      ))}

      <Panel
        id="duplicates"
        title={`Duplicate candidates · ${fmtCount(dupes.length)} open`}
        description={
          <>
            Exact stable keys (registry ID, VAT, domain) are resolved automatically at ingestion. Everything below is
            only a <em>possible</em> match and needs a reviewer: merge (same legal entity), link (related, e.g. same
            group) or dismiss (different companies).
          </>
        }
      >
        {dupes.length === 0 ? (
          <EmptyState>No open candidates.</EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Band / score</th>
                  <th>Possible match</th>
                  <th>Signals</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {dupes.map((d) => (
                  <tr key={d.id}>
                    <td className={styles.nowrap}>
                      <Badge value={d.band} label={`${d.band} · ${d.score}`} />
                    </td>
                    <td>
                      <div className={styles.match}>
                        <Link href={`/companies/${d.company_a_id}`}>{d.company_a_name ?? "Company A"}</Link>
                        <span className={styles.vsText}>vs</span>
                        <Link href={`/companies/${d.company_b_id}`}>{d.company_b_name ?? "Company B"}</Link>
                      </div>
                      <span className="cell-sub">Suggested {fmtDate(d.created_at)}</span>
                    </td>
                    <td>
                      <ul className={styles.signals}>
                        {d.reasons.map((r, i) => (
                          <li key={i} className={styles[effectKind(r.effect)]}>
                            <span className={styles.effect}>{r.effect}</span>
                            <strong>{humanise(r.signal)}</strong>
                            {r.detail}
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td>
                      <DuplicateActions d={d} />
                      <Link href={`/quality/duplicates/${d.id}`} className={styles.reviewLink}>
                        Review →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {done.length > 0 && (
          <details className={styles.resolved}>
            <summary>Resolved ({fmtCount(done.length)})</summary>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Status</th>
                    <th>Companies</th>
                    <th>Resolution</th>
                    <th aria-label="Review" />
                  </tr>
                </thead>
                <tbody>
                  {done.map((d) => (
                    <tr key={d.id}>
                      <td>
                        <Badge value={d.status} />
                      </td>
                      <td className="small">
                        <div className={styles.match}>
                          <Link href={`/companies/${d.company_a_id}`}>{d.company_a_name ?? "Company A"}</Link>
                          <span className={styles.vsText}>vs</span>
                          <Link href={`/companies/${d.company_b_id}`}>{d.company_b_name ?? "Company B"}</Link>
                        </div>
                      </td>
                      <td className="small muted">
                        {d.resolved_by ?? "—"} · {fmtDate(d.resolved_at)} · {d.resolution_reason ?? "—"}
                      </td>
                      <td className={styles.nowrap}>
                        <Link href={`/quality/duplicates/${d.id}`} className="small">
                          Review →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </Panel>

      <div className="grid grid-2">
        <Panel
          title={`Unresolved conflicts · ${fmtCount(t.unresolved_conflicts ?? report.conflicts.length)}`}
          description="Fields where current sources disagree. Every source claim stays visible; a manual correction is recorded separately."
        >
          {report.conflicts.length === 0 ? (
            <EmptyState>None.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>Field</th>
                    <th>Source claims</th>
                  </tr>
                </thead>
                <tbody>
                  {report.conflicts.map((cf) => (
                    <tr key={`${cf.company_id}-${cf.field_name}`}>
                      <td>
                        <Link href={`/companies/${cf.company_id}`}>
                          <strong>{cf.legal_name}</strong>
                        </Link>
                        {cf.resolved_by_correction && (
                          <span className="cell-sub">
                            <Badge value="manually-corrected" />
                          </span>
                        )}
                      </td>
                      <td>
                        <code>{cf.field_name}</code>
                      </td>
                      <td className="small">
                        <div className={styles.claims}>
                          {cf.values.map((v) => (
                            <span key={v.fact_id} className={styles.breakAll}>
                              {fmtValue(v.value)}{" "}
                              <span className="muted">
                                — {v.source_id}, {fmtDate(v.observed_at)}
                              </span>
                            </span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          title="Missing fields"
          description="Companies with no value for each tracked field, most frequent first."
          footnote="Missing values reduce completeness; they are never filled by guessing."
        >
          {missing.length === 0 ? (
            <EmptyState>None.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th className="num">Companies missing</th>
                    <th>Examples</th>
                  </tr>
                </thead>
                <tbody>
                  {missing.map(([k, v]) => (
                    <tr key={k}>
                      <td className={styles.nowrap}>
                        <strong>{v.label}</strong>
                      </td>
                      <td className="num">{fmtCount(v.count)}</td>
                      <td className="small">
                        {v.count === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          <div className={styles.examples}>
                            {v.companies.slice(0, 6).map((c) => (
                              <Link key={c.company_id} href={`/companies/${c.company_id}`}>
                                {c.legal_name}
                              </Link>
                            ))}
                            {v.count > 6 && <span className="muted">+{fmtCount(v.count - 6)} more</span>}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <div className="grid grid-2">
        <Panel
          title={`Validation failures · ${fmtCount(t.validation_failures ?? failures.length)}`}
          description="Source rows rejected by validation, linked to the ingestion run that recorded them."
          footnote={
            failures.length > VALIDATION_LIMIT
              ? `Showing the first ${VALIDATION_LIMIT} of ${fmtCount(failures.length)} listed failures.`
              : undefined
          }
        >
          {failures.length === 0 ? (
            <EmptyState>None.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Run row</th>
                    <th>Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {failures.slice(0, VALIDATION_LIMIT).map((v, i) => (
                    <tr key={i}>
                      <td className="small">
                        <Link href={`/runs/${v.run_id}`} className={styles.nowrap}>
                          {v.source_id} row {v.row}
                        </Link>
                        {v.source_key && <span className={`cell-sub mono ${styles.breakAll}`}>{v.source_key}</span>}
                      </td>
                      <td className="small error">{v.errors.join("; ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <div className={styles.col}>
          <Panel
            title={`Stale records · ${fmtCount(staleTotal)}`}
            description="Companies never verified, or not verified within the configured staleness window."
            footnote={
              report.stale_records.length < staleTotal
                ? `Showing ${fmtCount(report.stale_records.length)} of ${fmtCount(staleTotal)}.`
                : undefined
            }
          >
            {report.stale_records.length === 0 ? (
              <EmptyState>None.</EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Record</th>
                      <th>Last verified</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.stale_records.map((s) => (
                      <tr key={s.company_id}>
                        <td>
                          <Link href={`/companies/${s.company_id}`}>
                            <strong>{s.legal_name}</strong>
                          </Link>
                        </td>
                        <td className="small muted">{fmtDate(s.last_verified_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Most frequent warnings"
            description="Warnings recorded on ingestion records, grouped by message."
          >
            {report.top_warnings.length === 0 ? (
              <EmptyState>None.</EmptyState>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Warning</th>
                      <th className="num">Count</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.top_warnings.map((w) => (
                      <tr key={w.message}>
                        <td className="small">{w.message}</td>
                        <td className="num">{fmtCount(w.count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
