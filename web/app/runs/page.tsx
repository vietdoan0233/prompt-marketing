import Link from "next/link";

import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader, Panel } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { IngestionRun, Source } from "@/lib/types";

import { fmtDay, fmtInt, querySummary, retryBlockedReason, shortHash } from "./format";
import { RunForms } from "./RunForms";
import styles from "./runs.module.css";

export const dynamic = "force-dynamic";

/** A run counter; a counter the run did not record shows "—", never 0. */
function N({ n, tone }: { n: number | undefined; tone?: "good" | "warn" | "bad" }) {
  return <span className={`count${typeof n === "number" && n > 0 && tone ? ` count-${tone}` : ""}`}>{fmtInt(n)}</span>;
}

// The list endpoint returns the newest runs first and has no offset, so older runs are not reachable from here.
const RUN_LIMIT = 200;

export default async function RunsPage() {
  const [runs, sources] = await Promise.all([
    apiGet<IngestionRun[]>("/ingestion-runs", { limit: RUN_LIMIT }),
    apiGet<Source[]>("/sources"),
  ]);
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Data ingestion" }, { label: "Ingestion runs" }]}
        title="Ingestion runs"
        subtitle="Import the approved Estonian register datasets or a Mergero-supplied CSV. Each run records its input hash and gate outcome."
      />
      <RunForms sources={sources} />
      <Panel
        title="Recent ingestion runs"
        description="Import status, accepted and rejected records, warnings and retry history."
        actions={
          <span className="muted small">
            {runs.length < RUN_LIMIT
              ? `${fmtInt(runs.length)} ${runs.length === 1 ? "run" : "runs"} · newest first (list shows up to ${RUN_LIMIT})`
              : `Latest ${RUN_LIMIT} runs · older runs are not listed`}
          </span>
        }
        footnote={
          <>
            Runs move IMPORT_STARTED → FETCHED → PARSED → VALIDATED → UPSERTED, or stop as REJECTED at the permission
            gate. Reruns of the same snapshot are idempotent (source key + content hash). Retrying a register import
            re-queries the source as a new run; retrying a CSV import replays its retained upload and is unavailable
            once the raw input has expired or was never stored.
          </>
        }
      >
        {runs.length === 0 ? (
          <EmptyState>No runs yet. Run the Estonia import or import a CSV above.</EmptyState>
        ) : (
          <div className="table-wrap">
            <table className={styles.runsTable}>
              <thead>
                <tr>
                  <th>Started (UTC)</th>
                  <th>Source</th>
                  <th>File / query</th>
                  <th>Status</th>
                  <th className="num">Found</th>
                  <th className="num">Accepted</th>
                  <th className="num">Updated</th>
                  <th className="num">Unchanged</th>
                  <th className="num">Dupes</th>
                  <th className="num">Rejected</th>
                  <th className="num">Sub-scale</th>
                  <th className="num">Warnings</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const started = fmtDay(r.started_at);
                  const queryJson = JSON.stringify(r.query);
                  const blocked = retryBlockedReason(r);
                  const stages = [
                    ...new Set(r.errors.map((e) => e.stage?.replaceAll("_", " ")).filter((s): s is string => Boolean(s))),
                  ];
                  return (
                    <tr key={r.id}>
                      <td className={styles.started}>
                        <Link href={`/runs/${r.id}`} title={r.started_at}>
                          <strong>{started ? `${started.date} · ${started.time}` : "—"}</strong>
                        </Link>
                        <span className="sub">
                          run <span className="mono">{r.id.slice(0, 8)}</span>
                          {r.retry_of_id && (
                            <>
                              {" · "}
                              <Link
                                href={`/runs/${r.retry_of_id}`}
                                className={styles.retryMark}
                                title={`Retry of run ${r.retry_of_id}`}
                              >
                                retry
                              </Link>
                            </>
                          )}
                        </span>
                      </td>
                      <td>{r.source_id}</td>
                      <td>
                        {r.kind === "csv" ? (
                          <span className={styles.fileCell}>{r.file_name ?? "—"}</span>
                        ) : (
                          <span className={styles.fileCell} title={queryJson}>
                            {querySummary(r.query)}
                          </span>
                        )}
                        <span className="sub" title={r.input_hash ?? "No input hash recorded"}>
                          input hash <span className="mono">{shortHash(r.input_hash)}</span>
                        </span>
                      </td>
                      <td>
                        <Badge value={r.status} />
                        {r.status === "REJECTED" && <span className={styles.statusNote}>permission gate · nothing stored</span>}
                        {r.status === "FAILED" && (
                          <span className={styles.statusNote}>
                            {stages.length > 0 ? `failed at ${stages.join(", ")}` : "failure stage not recorded"}
                          </span>
                        )}
                        {r.errors.length > 0 && (
                          <span className={styles.statusErr} title={r.errors.map((e) => e.message).join("\n")}>
                            {r.errors[0].message.length > 70 ? `${r.errors[0].message.slice(0, 69)}…` : r.errors[0].message}
                          </span>
                        )}
                      </td>
                      <td className="num">
                        <N n={r.counts.discovered} />
                      </td>
                      <td className="num">
                        <N n={r.counts.accepted} tone="good" />
                      </td>
                      <td className="num">
                        <N n={r.counts.updated} />
                      </td>
                      <td className="num">
                        <N n={r.counts.unchanged} />
                      </td>
                      <td className="num">
                        <N n={r.counts.duplicates} tone="warn" />
                      </td>
                      <td className="num">
                        <N n={r.counts.rejected} tone="bad" />
                      </td>
                      <td className="num">
                        <N n={r.counts.sub_scale} />
                      </td>
                      <td className="num">
                        <N n={r.counts.warnings} />
                      </td>
                      <td className={styles.retryCell}>
                        <ActionButton
                          path={`/ingestion-runs/${r.id}/retry`}
                          label="Retry"
                          size="sm"
                          openRun
                          disabled={Boolean(blocked)}
                          disabledReason={blocked ? "Input not retained; re-upload the CSV" : undefined}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
