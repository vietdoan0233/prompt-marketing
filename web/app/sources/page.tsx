import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader, Panel, Stat } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { Source } from "@/lib/types";

import styles from "./sources.module.css";

export const dynamic = "force-dynamic";

const TIERS: { tier: Source["tier"]; title: string; blurb: string }[] = [
  { tier: "A", title: "A · Official registries & public APIs", blurb: "Preferred. Live where an open API exists; credential-gated registries fail closed until credentials are configured." },
  { tier: "C", title: "C · Company-website enrichment", blurb: "Opt-in only (disabled by default). The web-digital-decay check fetches a few pages of a company's own verified website (robots.txt respected, rate-limited) and stores extracted evidence only — never raw HTML. Enriches existing companies; never creates them." },
  { tier: "L", title: "L · Licensed / commercial", blurb: "Pending until a licence or API contract is signed and recorded as the approval reference." },
  { tier: "I", title: "I · Internal", blurb: "Mergero-supplied uploads and reviewer corrections." },
];

/** " · " with a non-breaking space before the dot, so wrapped field lists never start a line with "·". */
const NBSP_DOT = " · ";

function plural(n: number, one: string, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

export default async function SourcesPage() {
  const sources = await apiGet<Source[]>("/sources");
  const approved = sources.filter((s) => s.permission_status === "approved");
  const approvedEnabled = approved.filter((s) => s.enabled).length;
  const ingestible = sources.filter((s) => s.ingestible).length;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Data governance" }, { label: "Source registry" }]}
        title="Source registry"
        subtitle={
          <>
            Only approved, enabled sources inside their regional policy can ingest. Everything else fails closed at the
            permission gate, before any data is stored.
          </>
        }
      />

      <div className="stats">
        <Stat value={sources.length} label="Registered sources" sub="Catalogued by policy tier" />
        <Stat
          value={approved.length}
          tone="good"
          label="Approved sources"
          sub={`${approvedEnabled} enabled · ${approved.length - approvedEnabled} disabled`}
        />
        <Stat value={ingestible} tone="accent" label="Can ingest now" sub="Enabled and within policy" />
        <Stat
          value="Fail closed"
          tone="warn"
          label="Permission gate"
          sub="Disabled or unapproved data is not stored"
        />
      </div>

      {TIERS.map(({ tier, title, blurb }) => {
        const rows = sources
          .filter((s) => s.tier === tier)
          .sort((a, b) => Number(b.ingestible) - Number(a.ingestible) || a.id.localeCompare(b.id));
        const canIngest = rows.filter((s) => s.ingestible).length;
        return (
          <Panel
            key={tier}
            className={styles.tierPanel}
            title={title}
            description={blurb}
            actions={
              <span className={styles.tierCount}>
                {plural(rows.length, "source")} · {canIngest} can ingest
              </span>
            }
          >
            {rows.length === 0 ? (
              <EmptyState>No tier {tier} sources are registered.</EmptyState>
            ) : (
              <div className="table-wrap">
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Source</th>
                      <th>Coverage</th>
                      <th>Mode / type</th>
                      <th>Permission</th>
                      <th>Connector</th>
                      <th>Allowed fields</th>
                      <th className="num">Retention</th>
                      <th className="num">Trust</th>
                      <th className="num">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((s) => (
                      <tr key={s.id} className={s.ingestible ? "" : "row-muted"}>
                        <td className={styles.sourceCol}>
                          <strong className={styles.name}>{s.name}</strong>
                          <span className={styles.ident}>
                            <code>{s.id}</code> · {s.provider}
                          </span>
                          {s.terms_url && (
                            <a className={styles.terms} href={s.terms_url} target="_blank" rel="noreferrer">
                              terms ↗
                            </a>
                          )}
                          {s.notes && <div className={styles.notes}>{s.notes}</div>}
                        </td>
                        <td>
                          <div className="chips">
                            {s.countries.map((c) => (
                              <span key={c} className="chip">
                                {c}
                              </span>
                            ))}
                          </div>
                          <span className="cell-sub">{s.region}</span>
                        </td>
                        <td className={styles.modeCol}>
                          <div className={styles.lines}>
                            <span className={styles.primary}>{s.source_mode}</span>
                            <span className="muted">{s.source_type}</span>
                            <span className="muted">
                              <span className={styles.label}>base</span>
                              {s.base_confidence}
                            </span>
                          </div>
                        </td>
                        <td className={styles.permCol}>
                          <Badge value={s.permission_status} />
                          {s.approval_reference && <span className={styles.ref}>{s.approval_reference}</span>}
                          <span className={styles.policy}>
                            <span className={styles.label}>policy</span>
                            {s.usage_policy}
                          </span>
                        </td>
                        <td className={styles.connectorCol}>
                          <Badge
                            value={s.enabled ? "approved" : "unknown"}
                            label={s.enabled ? "enabled" : "disabled"}
                          />
                          <span className="cell-sub mono">{s.connector_type}</span>
                          {s.rate_limit_per_minute != null && (
                            <span className="cell-sub">{s.rate_limit_per_minute} req/min</span>
                          )}
                          {s.gate_reasons.length > 0 && (
                            <span className={styles.gate} title={s.gate_reasons.join("\n")}>
                              gate: {s.gate_reasons[0]}
                              {s.gate_reasons.length > 1 && (
                                <span className={styles.gateMore}> (+{s.gate_reasons.length - 1} more)</span>
                              )}
                            </span>
                          )}
                        </td>
                        <td className={styles.fieldsCol}>
                          {s.allowed_fields.length ? s.allowed_fields.join(NBSP_DOT) : <span className="muted">none</span>}
                          {Object.keys(s.field_mapping).length > 0 && (
                            <details>
                              <summary>column mapping</summary>
                              <div className={styles.mapping}>
                                {Object.entries(s.field_mapping).map(([k, v]) => (
                                  <div key={k} className="mono">
                                    {k} → {v}
                                  </div>
                                ))}
                              </div>
                            </details>
                          )}
                        </td>
                        <td className={`num ${styles.retention}`}>{s.retention_days} d</td>
                        <td className={`num ${styles.trust}`}>{s.trust_rank}</td>
                        <td className={styles.actionCell}>
                          {s.connector_type !== "manual" &&
                            (s.enabled ? (
                              <ActionButton path={`/sources/${s.id}/disable`} label="Disable" variant="danger" size="sm" />
                            ) : (
                              <ActionButton path={`/sources/${s.id}/enable`} label="Enable" size="sm" />
                            ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        );
      })}

      <p className="panel-foot">
        Trust rank: lower = more authoritative (registries 10, manual corrections 0). Used to order conflicting values;
        conflicts are always shown, never silently resolved.
      </p>
    </>
  );
}
