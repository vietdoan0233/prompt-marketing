import Link from "next/link";
import { Fragment } from "react";

import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader, Panel, type Tone } from "@/components/ui";
import { apiGet, apiGetOrNotFound } from "@/lib/api";
import type { IngestionRun } from "@/lib/types";

import { counterSections, fmtInt, fmtStamp, querySummary, retryBlockedReason, shortHash } from "../format";
import styles from "../runs.module.css";

export const dynamic = "force-dynamic";

const STAGES = ["IMPORT_STARTED", "FETCHED", "PARSED", "VALIDATED", "UPSERTED"];
// The six record outcomes get serif figures; every other persisted counter is listed in groups below them.
const HEADLINE: { key: string; tone?: Tone }[] = [
  { key: "discovered" },
  { key: "accepted", tone: "good" },
  { key: "updated", tone: "accent" },
  { key: "unchanged", tone: "muted" },
  { key: "duplicates", tone: "warn" },
  { key: "rejected", tone: "bad" },
];
const HEADLINE_KEYS = new Set(HEADLINE.map((h) => h.key));

// Large runs (the full register import has ~40k record outcomes) are paged so the page stays responsive;
// every record remains reachable through the outcome filter and page links.
const PAGE_SIZE = 250;
// The run list endpoint has no offset, so retries of this run are looked up among the latest runs only.
const RETRY_LOOKUP_LIMIT = 500;

type Search = { outcome?: string; page?: string };

export default async function RunDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Search>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const [run, recent] = await Promise.all([
    apiGetOrNotFound<IngestionRun>(`/ingestion-runs/${id}`),
    // Retries are secondary: if this lookup fails the page still renders and says so.
    apiGet<IngestionRun[]>("/ingestion-runs", { limit: RETRY_LOOKUP_LIMIT }).catch(() => null),
  ]);
  const retries = recent ? recent.filter((r) => r.retry_of_id === run.id) : null;
  const stageIndex = STAGES.indexOf(run.status);
  const rejected = run.status === "REJECTED";
  const failed = run.status === "FAILED";
  const errorStages = [...new Set(run.errors.map((e) => e.stage).filter((s): s is string => Boolean(s)))];
  const inputLabel = run.kind === "csv" ? (run.file_name ?? "—") : querySummary(run.query);
  const retryBlocked = retryBlockedReason(run);
  const sections = counterSections(run.counts, HEADLINE_KEYS);

  // record outcome filter + paging
  const records = run.records ?? [];
  const outcomeCounts = new Map<string, number>();
  for (const r of records) outcomeCounts.set(r.outcome, (outcomeCounts.get(r.outcome) ?? 0) + 1);
  const outcome = sp.outcome && outcomeCounts.has(sp.outcome) ? sp.outcome : undefined;
  const filtered = outcome ? records.filter((r) => r.outcome === outcome) : records;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const page = Math.min(pages, Math.max(1, Number.parseInt(sp.page ?? "1", 10) || 1));
  const shown = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const href = (o: string | undefined, p = 1) => {
    const q = new URLSearchParams();
    if (o) q.set("outcome", o);
    if (p > 1) q.set("page", String(p));
    const s = q.toString();
    return `/runs/${run.id}${s ? `?${s}` : ""}#records`;
  };

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Ingestion runs", href: "/runs" }, { label: "Run detail" }]}
        title={`Run ${run.id.slice(0, 8)}`}
        subtitle={
          <>
            {run.source_id} · <span title={JSON.stringify(run.query)}>{inputLabel}</span> · actor {run.actor} · input
            hash <span title={run.input_hash ?? undefined}>{shortHash(run.input_hash)}</span>
          </>
        }
        meta={<Badge value={run.status} large />}
        actions={
          <div className={styles.retryBox}>
            <ActionButton
              path={`/ingestion-runs/${run.id}/retry`}
              label="Retry run"
              variant="primary"
              openRun
              disabled={Boolean(retryBlocked)}
              disabledReason={retryBlocked}
            />
            {!retryBlocked && (
              <p className="form-hint">
                {run.kind === "csv"
                  ? "Retry replays the retained upload as a new run."
                  : "Retry re-queries the source as a new run; its results can differ from this run."}
              </p>
            )}
          </div>
        }
      />

      {rejected && (
        <div className="notice notice-bad">
          Rejected at the permission gate before any data was fetched. Nothing from this input was stored.
          <ul>
            {run.errors.map((e, i) => (
              <li key={i}>{e.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className={`grid ${styles.detailGrid}`}>
        <Panel title="Progress" description="Stage-by-stage import state and the parser/configuration used for this input.">
          {rejected ? (
            <div className={styles.stopped}>
              <span className="step failed">
                <b>REJECTED</b>
                <span className={styles.stepState}>permission gate</span>
              </span>
              <p>
                The permission gate stopped this run before fetching, so no pipeline stage ran and nothing was stored.
                The gate reasons are listed above.
              </p>
            </div>
          ) : failed ? (
            <div className={styles.stopped}>
              <span className="step failed">
                <b>FAILED</b>
                <span className={styles.stepState}>
                  {errorStages.length > 0
                    ? `recorded at ${errorStages.map((st) => st.replaceAll("_", " ")).join(", ")}`
                    : "stage not recorded"}
                </span>
              </span>
              <p>
                Stage completion before the failure was not recorded: the run keeps only its final status, so earlier
                stages are not shown as done or not reached. The errors are listed below; the counters show what was
                persisted when the run stopped.
              </p>
            </div>
          ) : stageIndex >= 0 ? (
            <>
              <div className={`stepper ${styles.stepFill}`}>
                {STAGES.map((s, i) => {
                  const done = stageIndex >= i;
                  return (
                    <Fragment key={s}>
                      {i > 0 && <span className={`step-link${done ? " done" : ""}`} />}
                      <span className={`step${done ? " done" : ""}`}>
                        <span className={styles.stepState}>{done ? "Done" : "Not reached"}</span>
                        <b>{s}</b>
                      </span>
                    </Fragment>
                  );
                })}
              </div>
              {run.status !== "UPSERTED" && !run.finished_at && (
                <p className={styles.stopNote}>
                  No finish time is recorded: the run is still in progress or was interrupted after {run.status}.
                </p>
              )}
            </>
          ) : (
            <p className={styles.stopNote}>Status {run.status} is not a pipeline stage, so stage progress is not shown.</p>
          )}
          <dl className="kv small">
            <dt>Started</dt>
            <dd>{fmtStamp(run.started_at)}</dd>
            <dt>Finished</dt>
            <dd>{fmtStamp(run.finished_at)}</dd>
            <dt>Parser version</dt>
            <dd className="mono">{run.parser_version}</dd>
            <dt>Config hash</dt>
            <dd className="mono" title={run.config_hash}>
              {run.config_hash.slice(0, 16)}
            </dd>
            <dt>Input hash</dt>
            <dd className="mono" title={run.input_hash ?? undefined}>
              {run.input_hash?.slice(0, 16) ?? "—"}
            </dd>
            <dt>{run.kind === "csv" ? "File" : "Query"}</dt>
            <dd className={run.kind === "csv" ? undefined : "mono"}>
              {run.kind === "csv" ? (run.file_name ?? "—") : JSON.stringify(run.query)}
            </dd>
            <dt>Raw input retention</dt>
            <dd>
              {run.input_retained
                ? `Retained until ${fmtStamp(run.input_expires_at)} (retry available)`
                : run.kind === "csv"
                  ? "Not retained (rejected before storage or expired); retry unavailable"
                  : "Not stored (retry re-queries the source)"}
            </dd>
            <dt>Min employees</dt>
            <dd>{run.min_employees}</dd>
            {run.retry_of_id && (
              <>
                <dt>Retry of</dt>
                <dd>
                  <Link href={`/runs/${run.retry_of_id}`} className="mono">
                    {run.retry_of_id.slice(0, 8)}
                  </Link>
                </dd>
              </>
            )}
            <dt>Retries</dt>
            <dd>
              {retries === null ? (
                <span className="muted">Could not be loaded</span>
              ) : retries.length === 0 ? (
                <span className="muted">None among the latest {RETRY_LOOKUP_LIMIT} runs</span>
              ) : (
                <span className={styles.retryLinks}>
                  {retries.map((r) => (
                    <Link key={r.id} href={`/runs/${r.id}`} className="mono" title={`${r.status} · ${r.started_at}`}>
                      {r.id.slice(0, 8)}
                    </Link>
                  ))}
                </span>
              )}
            </dd>
          </dl>
        </Panel>

        <Panel
          title="Outcome counts"
          description={
            failed
              ? "Counters as persisted when the run stopped."
              : rejected
                ? "Nothing was processed: the run was rejected before fetching."
                : "Input records resolved by the import pipeline."
          }
        >
          <div className={styles.figs}>
            {HEADLINE.map(({ key, tone }) => (
              <div key={key} className={styles.fig}>
                <div className={`${styles.figN}${tone ? ` ${tone === "muted" ? "muted" : `tone-text-${tone}`}` : ""}`}>
                  {fmtInt(run.counts[key])}
                </div>
                <div className={styles.figL}>{key.replaceAll("_", " ")}</div>
              </div>
            ))}
          </div>
          {sections.map((sec) => (
            <section key={sec.title}>
              <h3 className={styles.subhead}>{sec.title}</h3>
              <dl className={styles.miniCounts}>
                {sec.items.map(({ key, label, value }) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd className={value === 0 ? styles.zero : undefined}>{fmtInt(value)}</dd>
                  </div>
                ))}
              </dl>
              {sec.note && <p className={styles.countNote}>{sec.note}</p>}
            </section>
          ))}
        </Panel>
      </div>

      {run.errors.length > 0 && !rejected && (
        <Panel title="Errors">
          <ul>
            {run.errors.map((e, i) => (
              <li key={i} className="error">
                [{e.stage ?? "stage not recorded"}] {e.reference ? `${e.reference}: ` : ""}
                {e.message}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {run.warnings.length > 0 && (
        <Panel
          title="Run warnings"
          description="Dataset- and row-level warnings recorded while importing this input."
          actions={<span className="muted small">{fmtInt(run.warnings.length)} warnings</span>}
        >
          <ul className={styles.warnList}>
            {run.warnings.map((w, i) => (
              <li key={i}>
                {(w.row !== undefined || w.source_key) && (
                  <span className={styles.warnRef}>
                    {w.row !== undefined && `row ${w.row}`}
                    {w.row !== undefined && w.source_key && " · "}
                    {w.source_key}
                  </span>
                )}
                <span>{w.message}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        id="records"
        title="Record outcomes"
        description="Rejected records are explained by row; warnings remain visible beside accepted records."
        actions={<span className="muted small">{fmtInt(records.length)} records</span>}
      >
        {outcomeCounts.size > 1 && (
          <nav className={`tabs ${styles.outcomeTabs}`} aria-label="Filter records by outcome">
            <Link href={href(undefined)} className={outcome ? undefined : "active"}>
              All<span className="tab-count">{fmtInt(records.length)}</span>
            </Link>
            {[...outcomeCounts.entries()].map(([o, n]) => (
              <Link key={o} href={href(o)} className={outcome === o ? "active" : undefined}>
                {o.replaceAll("_", " ")}
                <span className="tab-count">{fmtInt(n)}</span>
              </Link>
            ))}
          </nav>
        )}
        {records.length === 0 ? (
          <EmptyState>No record outcomes were stored for this run.</EmptyState>
        ) : (
          <>
            <div className={`table-wrap ${styles.recordScroll}`}>
              <table className={styles.recordTable}>
                <thead>
                  <tr>
                    <th className="num">Row</th>
                    <th>Source key</th>
                    <th>Outcome</th>
                    <th>Qualification</th>
                    <th>Match / rejection reason</th>
                    <th>Warnings</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r, i) => (
                    <tr key={`${r.row_number}-${i}`}>
                      <td className="num">{r.row_number}</td>
                      <td className="small mono">
                        {r.company_id ? (
                          <Link href={`/companies/${r.company_id}`}>{r.source_key}</Link>
                        ) : (
                          (r.source_key ?? "—")
                        )}
                      </td>
                      <td>
                        <Badge value={r.outcome} />
                      </td>
                      <td>{r.qualification ? <Badge value={r.qualification} /> : <span className="faint">—</span>}</td>
                      <td className="small">
                        {r.errors.map((e, j) => (
                          <div key={`e${j}`} className={styles.reasonErr}>
                            {e}
                          </div>
                        ))}
                        {r.match_reasons.map((m, j) => (
                          <div key={`m${j}`} className={styles.reason}>
                            {m}
                          </div>
                        ))}
                        {r.errors.length === 0 && r.match_reasons.length === 0 && <span className="faint">—</span>}
                      </td>
                      <td className="small muted">
                        {r.warnings.length > 0 ? r.warnings.map((w, j) => <div key={j}>{w}</div>) : <span className="faint">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {pages > 1 && (
              <div className={`pagination ${styles.pager}`}>
                <PageLink to={1} current={page} max={pages} href={href(outcome, 1)} label="« First" />
                <PageLink to={page - 1} current={page} max={pages} href={href(outcome, page - 1)} label="← Previous" />
                <span className="muted">
                  Rows {fmtInt((page - 1) * PAGE_SIZE + 1)}–{fmtInt(Math.min(page * PAGE_SIZE, filtered.length))} of{" "}
                  {fmtInt(filtered.length)}
                  {outcome ? ` ${outcome.replaceAll("_", " ")}` : ""} · page {page} of {fmtInt(pages)}
                </span>
                <PageLink to={page + 1} current={page} max={pages} href={href(outcome, page + 1)} label="Next →" />
                <PageLink to={pages} current={page} max={pages} href={href(outcome, pages)} label="Last »" />
                <form method="get" action={`/runs/${run.id}#records`} className={styles.jump}>
                  {outcome && <input type="hidden" name="outcome" value={outcome} />}
                  <label>
                    <span className="visually-hidden">Go to page</span>
                    <input name="page" type="number" min={1} max={pages} defaultValue={page} />
                  </label>
                  <button className="btn btn-sm" type="submit">
                    Go
                  </button>
                </form>
              </div>
            )}
          </>
        )}
      </Panel>
    </>
  );
}

function PageLink({
  to,
  current,
  max,
  href,
  label,
}: {
  to: number;
  current: number;
  max: number;
  href: string;
  label: string;
}) {
  const disabled = to === current || to < 1 || to > max;
  return disabled ? (
    <span className={`btn btn-sm ${styles.pageOff}`} aria-disabled="true">
      {label}
    </span>
  ) : (
    <Link className="btn btn-sm" href={href}>
      {label}
    </Link>
  );
}
