import { Badge } from "@/components/Badge";
import { EmptyState, Panel } from "@/components/ui";
import { fmtDate, fmtMoney, fmtValue } from "@/lib/api";
import type { CompanyDetail, Financial, RegisteredAddress } from "@/lib/types";

import styles from "./company.module.css";
import { FactRow, FactTableHead, RevenueTrend, RunLink } from "./shared";

export function AddressKv({ a, showValidFrom }: { a: RegisteredAddress; showValidFrom?: boolean }) {
  return (
    <dl className={`kv ${styles.kv}`}>
      <dt>Address</dt>
      <dd>{a.address_line ?? "—"}</dd>
      <dt>Postal code</dt>
      <dd>{a.postal_code ?? "—"}</dd>
      <dt>City</dt>
      <dd>{a.city ?? "—"}</dd>
      <dt>Municipality</dt>
      <dd>{a.municipality ?? "—"}</dd>
      <dt>County</dt>
      <dd>{a.county ?? "—"}</dd>
      <dt>EHAK code</dt>
      <dd className="mono">{a.ehak_code ?? "—"}</dd>
      <dt>Country</dt>
      <dd>{a.country ?? "—"}</dd>
      {showValidFrom && (
        <>
          <dt>Valid from</dt>
          <dd>
            {fmtDate(a.valid_from)}
            {a.valid_to ? <span className="cell-sub">valid to {fmtDate(a.valid_to)}</span> : <span className="cell-sub">current version</span>}
          </dd>
        </>
      )}
      <dt>Provenance</dt>
      <dd>
        <a href={a.source_url} target="_blank" rel="noreferrer" className={styles.breakAll}>
          {a.source_file ?? a.source_id}
        </a>
        <span className="cell-sub">
          {a.source_name ?? a.source_id} · observed {fmtDate(a.observed_at)}
          {a.ingestion_run_id && (
            <>
              {" · "}
              <RunLink runId={a.ingestion_run_id} />
            </>
          )}
        </span>
      </dd>
      {a.warnings.length > 0 && (
        <>
          <dt>Source warnings</dt>
          <dd className="tone-text-warn">{a.warnings.join("; ")}</dd>
        </>
      )}
    </dl>
  );
}

/** Fallback wording of the importer's derivation when a derived row carries no formula text. */
const DERIVED_FORMULA = "operating_profit − depreciation_and_impairment";

/**
 * EBITDA as the API gives it. A missing value is unknown, never labelled reported; the derived formula is the signed
 * one recorded by the importer (the D&A line keeps its reported sign, so subtracting a negative expense adds it back).
 */
function EbitdaCell({ f }: { f: Financial }) {
  const cur = f.currency ?? "EUR";
  if (f.ebitda === null) {
    return (
      <>
        <span className="muted">—</span>
        <span className="cell-sub">Not reported or derivable</span>
      </>
    );
  }
  const derived = f.value_type === "derived";
  return (
    <>
      <strong>{fmtMoney(f.ebitda, cur)}</strong>
      <span className="cell-sub">
        <Badge
          value={f.value_type}
          label={derived ? "Derived" : f.value_type === "reported" ? "Reported" : f.value_type}
          tone={derived ? "info" : "muted"}
        />
      </span>
      {derived && (
        <>
          <span className={`cell-sub mono ${styles.formula}`}>
            {f.calculation_formula?.replace(" - ", " − ") ?? DERIVED_FORMULA}
          </span>
          {f.operating_profit !== null && f.depreciation_and_impairment !== null && (
            <span className={`cell-sub ${styles.formula}`}>
              {fmtMoney(f.operating_profit, cur)} − ({fmtMoney(f.depreciation_and_impairment, cur)})
            </span>
          )}
        </>
      )}
    </>
  );
}

export function ProfileTab({ d }: { d: CompanyDetail }) {
  const conflicts = d.fields.filter((f) => f.status === "conflicting");

  return (
    <>
      <div className={styles.profileRow}>
        <Panel
          title="Registered address"
          description="The official registered seat from the Estonian register, not an inferred operating location."
        >
          {d.registered_address ? (
            <AddressKv a={d.registered_address} />
          ) : (
            <EmptyState>No registered address was supplied.</EmptyState>
          )}
        </Panel>

        <Panel
          title="Financials by year"
          description={
            <>
              Amounts are formatted in EUR. Reported values keep their source signs and provenance. Reported EBITDA
              takes precedence; otherwise EBITDA = operating profit − the signed depreciation/impairment line (adding
              back a negative expense); without either, EBITDA stays unknown. Values are not annualized.
            </>
          }
          actions={<RevenueTrend rows={d.financials} />}
        >
          {d.financials.length > 0 ? (
            <div className="table-wrap">
              <table className={`financial-table ${styles.financialTable}`}>
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
                  {d.financials.map((f) => {
                    const cur = f.currency ?? "EUR";
                    return (
                      <tr key={f.id}>
                        <td>
                          <strong className={styles.year}>{f.fiscal_year}</strong>
                        </td>
                        <td className="small">
                          {f.period_start ?? "—"} – {f.period_end ?? "—"}
                          <span className="cell-sub">
                            {f.period_days === null
                              ? "period length unknown"
                              : `${f.period_days} days · ${
                                  f.period_length_class === "standard_12_month"
                                    ? "standard 12-month"
                                    : (f.period_length_class ?? "unclassified")
                                }`}
                          </span>
                          <span className="cell-sub">{f.statement_scope ?? "scope unknown"}</span>
                        </td>
                        <td className="num">{fmtMoney(f.revenue, cur)}</td>
                        <td className="num">{fmtMoney(f.net_income, cur)}</td>
                        <td className="num">{fmtMoney(f.operating_profit, cur)}</td>
                        <td className="num">{fmtMoney(f.depreciation_and_impairment, cur)}</td>
                        <td className="num">
                          <EbitdaCell f={f} />
                        </td>
                        <td className="small financial-source">
                          <a href={f.source_url} target="_blank" rel="noreferrer">
                            {f.source_file ?? f.source_id}
                          </a>
                          <span className="cell-sub mono">{f.source_key}</span>
                          {f.ingestion_run_id && (
                            <span className="cell-sub">
                              <RunLink runId={f.ingestion_run_id} className="muted" />
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No annual financial lines were imported.</EmptyState>
          )}
        </Panel>
      </div>

      <div className={styles.profileLower}>
        <Panel
          title="Consolidated profile"
          description="Each value is resolved from source facts; the label says how well it is supported."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Value</th>
                  <th>Label</th>
                  <th>Sources</th>
                </tr>
              </thead>
              <tbody>
                {d.fields.map((f) => (
                  <tr key={f.field_name}>
                    <td className="mono small muted">{f.field_name}</td>
                    <td>
                      {f.value === null ? (
                        <span className="muted">unknown</span>
                      ) : (
                        <span className={styles.factValue}>{fmtValue(f.value)}</span>
                      )}
                      {f.conflicting_values.length > 0 && (
                        <span className="cell-sub tone-text-bad">
                          also claimed: {f.conflicting_values.map((v) => fmtValue(v)).join(" · ")}
                        </span>
                      )}
                    </td>
                    <td>
                      <Badge value={f.label} />
                    </td>
                    <td className="small muted mono">{f.source_ids.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.revenue === null && (
            <p className="panel-foot">
              Consolidated revenue field: unknown (no source fact supplies it), so no valuation or revenue claim is
              made from it.{" "}
              {d.financials.length > 0
                ? "Filed annual statement lines are listed separately under Financials by year."
                : "No annual statements were imported for this company either."}
            </p>
          )}
        </Panel>

        {conflicts.length > 0 && (
          <Panel
            title="Conflicts"
            description="Sources disagree on these fields. Each claim is shown with its source."
            footnote="Resolve by adding a correction on the Review & correction tab. Source facts are never overwritten."
          >
            <div className="stack">
              {conflicts.map((f) => (
                <div key={f.field_name} className="subpanel">
                  <div className={styles.subHead}>
                    <strong className="mono">{f.field_name}</strong>
                    <Badge value="conflicting" />
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Value</th>
                          <th>Source</th>
                          <th>Observed</th>
                          <th>Confidence</th>
                        </tr>
                      </thead>
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
                </div>
              ))}
            </div>
          </Panel>
        )}
      </div>

      <Panel
        title="Source-backed facts"
        description="Every material fact with its source, reference, ingestion run, observation date, confidence, review status and usage policy."
        footnote="Source facts are never silently overwritten. Corrections are additive and preserve original provenance."
      >
        <div className="table-wrap">
          <table className={styles.factsTable}>
            <FactTableHead />
            <tbody>
              {d.facts.map((f) => (
                <FactRow key={f.id} f={f} />
              ))}
            </tbody>
          </table>
        </div>
        {d.history.length > 0 && (
          <details className={styles.history}>
            <summary>Superseded versions ({d.history.length})</summary>
            <div className="table-wrap">
              <table className={styles.factsTable}>
                <FactTableHead />
                <tbody>
                  {d.history.map((f) => (
                    <FactRow key={f.id} f={f} />
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
      </Panel>
    </>
  );
}
