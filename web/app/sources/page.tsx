import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { apiGet } from "@/lib/api";
import type { Source } from "@/lib/types";

export const dynamic = "force-dynamic";

const TIERS: { tier: Source["tier"]; title: string; blurb: string }[] = [
  { tier: "A", title: "A — Official registries & public APIs", blurb: "Preferred. Live where an open API exists; credential-gated registries fail closed until credentials are configured." },
  { tier: "C", title: "C — Company-website enrichment", blurb: "Crawls registry-published domains only (robots.txt respected). Enriches existing companies; never creates them." },
  { tier: "L", title: "L — Licensed / commercial", blurb: "Pending until a licence or API contract is signed and recorded as the approval reference." },
  { tier: "I", title: "Internal", blurb: "Mergero-supplied uploads and reviewer corrections." },
];

export default async function SourcesPage() {
  const sources = await apiGet<Source[]>("/sources");
  const approved = sources.filter((s) => s.permission_status === "approved").length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Source registry</h1>
          <p className="subtitle">
            Only <Badge value="approved" /> sources that are enabled and inside their regional policy can ingest.
            Everything else fails closed at the permission gate, before any data is stored.
          </p>
        </div>
      </div>
      <p className="small muted">
        {sources.length} sources registered · {approved} approved · {sources.filter((s) => s.ingestible).length} can ingest now
      </p>
      {TIERS.map(({ tier, title, blurb }) => (
        <section className="panel" key={tier}>
          <h2>{title}</h2>
          <p className="small muted" style={{ marginTop: -6 }}>
            {blurb}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th>Coverage</th>
                  <th>Mode / type</th>
                  <th>Permission</th>
                  <th>Connector</th>
                  <th>Allowed fields</th>
                  <th>Retention</th>
                  <th>Trust</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sources
                  .filter((s) => s.tier === tier)
                  .sort((a, b) => Number(b.ingestible) - Number(a.ingestible) || a.id.localeCompare(b.id))
                  .map((s) => (
                    <tr key={s.id} className={s.ingestible ? "" : "row-muted"}>
                      <td>
                        <strong>{s.name}</strong>
                        <div className="small muted">
                          <code>{s.id}</code> · {s.provider}
                        </div>
                        {s.terms_url && (
                          <a className="small" href={s.terms_url} target="_blank" rel="noreferrer">
                            terms
                          </a>
                        )}
                        {s.notes && <div className="small muted">{s.notes}</div>}
                      </td>
                      <td>
                        <div className="chips">
                          {s.countries.map((c) => (
                            <span key={c} className="chip">
                              {c}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="small">
                        {s.source_mode}
                        <div className="muted">{s.source_type}</div>
                        <div className="muted">base: {s.base_confidence}</div>
                      </td>
                      <td>
                        <Badge value={s.permission_status} />
                        {s.approval_reference && <div className="small muted mono">{s.approval_reference}</div>}
                        <div className="small muted">policy: {s.usage_policy}</div>
                      </td>
                      <td>
                        <Badge
                          value={s.enabled ? "approved" : "unknown"}
                          label={s.enabled ? "enabled" : "disabled"}
                        />
                        <div className="small muted">{s.connector_type}</div>
                        {s.gate_reasons.length > 0 && (
                          <div className="small error" title={s.gate_reasons.join("\n")}>
                            gate: {s.gate_reasons[0]}
                          </div>
                        )}
                      </td>
                      <td className="small">
                        {s.allowed_fields.length ? s.allowed_fields.join(", ") : <span className="muted">none</span>}
                        {Object.keys(s.field_mapping).length > 0 && (
                          <details>
                            <summary>column mapping</summary>
                            {Object.entries(s.field_mapping).map(([k, v]) => (
                              <div key={k} className="mono">
                                {k} → {v}
                              </div>
                            ))}
                          </details>
                        )}
                      </td>
                      <td className="num">
                        {s.retention_days} d
                      </td>
                      <td className="num">{s.trust_rank}</td>
                      <td>
                        {s.connector_type !== "manual" &&
                          (s.enabled ? (
                            <ActionButton path={`/sources/${s.id}/disable`} label="Disable" variant="danger" />
                          ) : (
                            <ActionButton path={`/sources/${s.id}/enable`} label="Enable" />
                          ))}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      <p className="small muted">
        Trust rank: lower = more authoritative (registries 10, manual corrections 0). Used to order conflicting values;
        conflicts are always shown, never silently resolved.
      </p>
    </>
  );
}
