import Link from "next/link";

import { Badge } from "@/components/Badge";
import { EmptyState, Panel, Stat } from "@/components/ui";
import { fmtDate, fmtMoney, fmtValue } from "@/lib/api";
import type { CompanyDetail } from "@/lib/types";

import styles from "./company.module.css";
import { AddressKv } from "./ProfileTab";
import { RunLink } from "./shared";

export function OwnershipTab({ d }: { d: CompanyDetail }) {
  const c = d.company;
  const shareCapital = d.fields.find((f) => f.field_name === "share_capital") ?? null;
  const hasCapital = shareCapital !== null && shareCapital.value !== null;
  const persons = d.shareholders.filter((s) => s.holder_type === "person").length;
  const entities = d.shareholders.filter((s) => s.holder_type === "legal_entity").length;

  return (
    <>
      <Panel
        title="Ownership"
        description={
          <>
            Current share capital and shareholders (osanikud) from the official register. A person shareholder is
            shown by name and holding only — the register&apos;s national ID code is never stored.
          </>
        }
      >
        <div className="stats">
          <Stat
            value={hasCapital ? fmtValue(shareCapital.value) : "unknown"}
            label="Share capital"
            tone={hasCapital ? "accent" : "muted"}
            sub={shareCapital ? <Badge value={shareCapital.label} /> : "no share-capital fact"}
          />
          <Stat value={d.shareholders.length} label="Current shareholders" sub="as supplied by the register" />
          <Stat value={entities} label="Legal-entity holders" sub="registry code kept" />
          <Stat value={persons} label="Person holders" sub="name, role and holding only" />
        </div>

        {d.shareholders.length > 0 ? (
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
                      <strong>{s.holder_name}</strong>
                      {s.holder_registry_code && (
                        <span className="cell-sub mono">
                          {s.holder_registry_code}
                          {s.holder_country ? ` (${s.holder_country})` : ""}
                        </span>
                      )}
                      {s.role && <span className="cell-sub">{s.role}</span>}
                    </td>
                    <td className="small">
                      <Badge
                        value={s.holder_type}
                        label={
                          s.holder_type === "person" ? "Person" : s.holder_type === "legal_entity" ? "Company" : "Unknown"
                        }
                        tone={s.holder_type === "person" ? "warn" : s.holder_type === "legal_entity" ? "info" : "muted"}
                      />
                      {s.holding_type && <span className="cell-sub">{s.holding_type}</span>}
                    </td>
                    <td className="num">{fmtMoney(s.holding_amount, s.holding_currency ?? "EUR")}</td>
                    <td className="num">{s.holding_percent === null ? "—" : `${s.holding_percent}%`}</td>
                    <td className="small">{fmtDate(s.effective_from)}</td>
                    <td className="small">
                      <a href={s.source_url} target="_blank" rel="noreferrer" className={styles.breakAll}>
                        {s.source_file ?? s.source_id}
                      </a>
                      <span className="cell-sub">observed {fmtDate(s.observed_at)}</span>
                      {s.ingestion_run_id && (
                        <span className="cell-sub">
                          <RunLink runId={s.ingestion_run_id} className="muted" />
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState>No current shareholders were supplied.</EmptyState>
        )}
        <p className="panel-foot">
          Rows marked with an amber edge are person records: personal data holding a name, role and holding only.
        </p>
      </Panel>

      <div className="grid grid-2">
        <Panel
          title="Stable identity keys"
          description={
            <>
              Stable keys used for entity resolution. Derived keys (e.g. VAT from a Finnish Y-tunnus) are matching keys
              only, never displayed as facts.
            </>
          }
          footnote="Identifiers are matching keys, never a substitute for a source-backed fact."
        >
          {d.identifiers.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Value</th>
                    <th>Type</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {d.identifiers.map((i) => (
                    <tr key={`${i.kind}:${i.value}`}>
                      <td className="small mono muted">{i.kind}</td>
                      <td className={`mono small ${styles.breakAll}`}>
                        <span className="tone-text-accent">{i.value}</span>
                      </td>
                      <td>
                        {i.derived ? (
                          <Badge value="derived" label="derived" tone="info" />
                        ) : (
                          <Badge value="source-backed" label="source-backed" tone="good" />
                        )}
                      </td>
                      <td className="small mono muted">{i.source_id ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No identifiers recorded.</EmptyState>
          )}

          <h3>Duplicate candidates</h3>
          {d.duplicates.length === 0 ? (
            <p className="small muted">No duplicate candidates involve this company.</p>
          ) : (
            <div className="stack">
              {d.duplicates.map((dup) => {
                const otherId = dup.company_a_id === c.id ? dup.company_b_id : dup.company_a_id;
                const otherName = dup.company_a_id === c.id ? dup.company_b_name : dup.company_a_name;
                return (
                  <div key={dup.id} className="subpanel">
                    <div className={styles.subHead}>
                      <Link href={`/companies/${otherId}`}>
                        <strong>{otherName ?? otherId}</strong>
                      </Link>
                      <span>
                        <Badge value={dup.status} /> <Badge value={dup.band} label={`${dup.band} ${dup.score}`} />
                      </span>
                    </div>
                    <ul className={styles.reasons}>
                      {dup.reasons.map((r, i) => (
                        <li
                          key={i}
                          className={`reason-${r.effect === "for" ? "for" : r.effect === "against" ? "against" : "neutral"}`}
                        >
                          <span className={styles.reasonEffect}>{r.effect}</span> {r.detail}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
          )}
          {d.duplicates.length > 0 && (
            <p className="small" style={{ marginTop: 12 }}>
              <Link href="/quality#duplicates">Review in data quality →</Link>
            </p>
          )}
        </Panel>

        <Panel
          title="Registered address"
          description="Official registered seat (asukoht). Address changes are versioned at import; this is the current version."
        >
          {d.registered_address ? (
            <AddressKv a={d.registered_address} showValidFrom />
          ) : (
            <EmptyState>No registered address was supplied.</EmptyState>
          )}
        </Panel>
      </div>
    </>
  );
}
