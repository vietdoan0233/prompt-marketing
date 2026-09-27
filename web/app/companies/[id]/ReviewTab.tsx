import { Badge } from "@/components/Badge";
import { EmptyState, Panel } from "@/components/ui";
import { fmtDate, fmtValue } from "@/lib/api";
import type { CompanyDetail, TimelineEntry } from "@/lib/types";

import { CorrectionForm } from "./ClientControls";
import styles from "./company.module.css";
import { EvidenceLink, fmtDetails } from "./shared";

const TIMELINE_LIMIT = 60;

function TimelineItem({ t }: { t: TimelineEntry }) {
  const sourceUrl = typeof t.detail.source_url === "string" ? t.detail.source_url : null;
  return (
    <li className={`k-${t.kind}`}>
      <span className="small muted">{fmtDate(t.at, true)}</span>
      <strong className="small">{t.title}</strong>
      <span className="small muted">
        {t.kind !== "audit" && "value" in t.detail ? (
          <>
            {t.source_id && <span className="mono">{t.source_id}</span>}
            {t.source_id && " · "}
            <span className={styles.timelineValue}>{fmtValue(t.detail.value)}</span>{" "}
            <Badge value={String(t.detail.confidence)} />
            {typeof t.detail.reason === "string" && t.detail.reason && (
              <span className="cell-sub">reason: {t.detail.reason}</span>
            )}
            {sourceUrl && (
              <span className="cell-sub">
                <EvidenceLink url={sourceUrl} label="source" />
              </span>
            )}
          </>
        ) : (
          <>
            {t.source_id && (
              <>
                <span className="mono">{t.source_id}</span>
                {" · "}
              </>
            )}
            {fmtDetails(t.detail)}
          </>
        )}
      </span>
    </li>
  );
}

export function ReviewTab({ d }: { d: CompanyDetail }) {
  const current = d.facts.filter((f) => !f.valid_to);
  const latest = d.timeline.slice(0, TIMELINE_LIMIT);
  const older = d.timeline.slice(TIMELINE_LIMIT);

  return (
    <>
      <div className={styles.reviewRow}>
        <Panel
          title="Current source facts"
          description="The facts a correction can target. Full provenance is on the Profile & financials tab."
        >
          {current.length > 0 ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Value</th>
                    <th>Source</th>
                    <th>Confidence</th>
                    <th>Observed</th>
                  </tr>
                </thead>
                <tbody>
                  {current.map((f) => (
                    <tr key={f.id}>
                      <td className="mono small">{f.field_name}</td>
                      <td>
                        <span className={styles.factValue}>{fmtValue(f.value_json)}</span>
                        {f.is_correction && f.correction_reason && (
                          <span className="cell-sub tone-text-info">reason: {f.correction_reason}</span>
                        )}
                      </td>
                      <td className="small">
                        {f.source_name ?? f.source_id}
                        <span className="cell-sub mono">{f.source_id}</span>
                      </td>
                      <td>
                        <Badge value={f.confidence} />
                      </td>
                      <td className="small">{fmtDate(f.observed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState>No current source facts.</EmptyState>
          )}
        </Panel>

        <Panel
          title="Add manual correction"
          description={
            <>
              Adds a <Badge value="manually-corrected" /> fact. The original facts, their values and provenance stay
              intact and visible.
            </>
          }
        >
          <CorrectionForm companyId={d.company.id} facts={d.facts} />
        </Panel>
      </div>

      <div className="grid grid-2">
        <Panel
          title="Evidence timeline"
          description="Facts observed, corrections added and manual actions, newest first."
          footnote={
            d.timeline.length > TIMELINE_LIMIT
              ? `The latest ${TIMELINE_LIMIT} of ${d.timeline.length.toLocaleString("en")} entries are listed first; older entries are under “Show older entries”.`
              : undefined
          }
        >
          {d.timeline.length > 0 ? (
            <>
              <div className={styles.legend}>
                <span className={styles.legendFact}>source fact</span>
                <span className={styles.legendCorrection}>correction</span>
                <span className={styles.legendAudit}>manual action</span>
              </div>
              <ul className="timeline">
                {latest.map((t, i) => (
                  <TimelineItem key={i} t={t} />
                ))}
              </ul>
              {older.length > 0 && (
                <details className={styles.olderEntries}>
                  <summary>
                    Show {older.length.toLocaleString("en")} older {older.length === 1 ? "entry" : "entries"}
                  </summary>
                  <ul className="timeline">
                    {older.map((t, i) => (
                      <TimelineItem key={i} t={t} />
                    ))}
                  </ul>
                </details>
              )}
            </>
          ) : (
            <EmptyState>No evidence recorded yet.</EmptyState>
          )}
        </Panel>

        <Panel title="Audit history" description="Every manual action on this company, with the recorded actor and details.">
          {d.audit_events.length === 0 ? (
            <EmptyState>No manual actions yet.</EmptyState>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {d.audit_events.map((a) => (
                    <tr key={a.id}>
                      <td className="small">{fmtDate(a.occurred_at, true)}</td>
                      <td className="small mono tone-text-accent">{a.action}</td>
                      <td className="small">{a.actor}</td>
                      <td className={`small muted ${styles.details}`}>{fmtDetails(a.details)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
