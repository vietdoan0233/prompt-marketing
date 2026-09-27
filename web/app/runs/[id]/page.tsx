import Link from "next/link";

import { ActionButton } from "@/components/ActionButton";
import { Badge } from "@/components/Badge";
import { apiGet, fmtDate } from "@/lib/api";
import type { IngestionRun } from "@/lib/types";

export const dynamic = "force-dynamic";

const STAGES = ["IMPORT_STARTED", "FETCHED", "PARSED", "VALIDATED", "UPSERTED"];
const COUNT_ORDER = [
  "discovered", "accepted", "updated", "unchanged", "duplicates", "rejected", "facts_added", "facts_changed",
  "contacts_added", "contacts_suppressed", "duplicate_candidates", "qualified", "borderline", "below_threshold",
  "sub_scale", "unknown_headcount", "warnings",
];

export default async function RunDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await apiGet<IngestionRun>(`/ingestion-runs/${id}`);
  const stageIndex = STAGES.indexOf(run.status);
  return (
    <>
      <div className="page-head">
        <div>
          <p className="small">
            <Link href="/runs">← Ingestion runs</Link>
          </p>
          <h1>
            Run <code>{run.id.slice(0, 8)}</code> <Badge value={run.status} />
          </h1>
          <p className="subtitle">
            <code>{run.source_id}</code> · {run.kind === "csv" ? run.file_name : JSON.stringify(run.query)} · by{" "}
            {run.actor}
          </p>
        </div>
        <ActionButton
          path={`/ingestion-runs/${run.id}/retry`}
          label="Retry run"
          variant="primary"
          openRun
        />
      </div>

      {run.status === "REJECTED" && (
        <div className="notice notice-bad">
          Rejected by the permission gate. Nothing from this input was stored.
          <ul>
            {run.errors.map((e, i) => (
              <li key={i}>{e.message}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-2">
        <section className="panel">
          <h2>Progress</h2>
          <div className="chips" style={{ marginBottom: 10 }}>
            {STAGES.map((s, i) => (
              <Badge key={s} value={stageIndex >= i ? "accepted" : "unknown"} label={s} />
            ))}
          </div>
          <dl className="kv small">
            <dt>Started</dt>
            <dd>{fmtDate(run.started_at, true)}</dd>
            <dt>Finished</dt>
            <dd>{fmtDate(run.finished_at, true)}</dd>
            <dt>Parser version</dt>
            <dd className="mono">{run.parser_version}</dd>
            <dt>Config hash</dt>
            <dd className="mono">{run.config_hash.slice(0, 16)}</dd>
            <dt>Input hash</dt>
            <dd className="mono">{run.input_hash?.slice(0, 16) ?? "—"}</dd>
            <dt>Raw input</dt>
            <dd>
              {run.input_retained
                ? `retained until ${fmtDate(run.input_expires_at)} (retry available)`
                : "not retained (expired or never stored)"}
            </dd>
            <dt>Min employees</dt>
            <dd>{run.min_employees}</dd>
            {run.retry_of_id && (
              <>
                <dt>Retry of</dt>
                <dd>
                  <Link href={`/runs/${run.retry_of_id}`}>{run.retry_of_id.slice(0, 8)}</Link>
                </dd>
              </>
            )}
          </dl>
        </section>
        <section className="panel">
          <h2>Counts</h2>
          <dl className="kv small">
            {COUNT_ORDER.map((k) => (
              <div key={k} style={{ display: "contents" }}>
                <dt>{k.replaceAll("_", " ")}</dt>
                <dd className="mono">{run.counts[k] ?? 0}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>

      {run.errors.length > 0 && run.status !== "REJECTED" && (
        <section className="panel">
          <h2>Errors</h2>
          <ul>
            {run.errors.map((e, i) => (
              <li key={i} className="error">
                [{e.stage}] {e.reference ? `${e.reference}: ` : ""}
                {e.message}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>Records ({run.records?.length ?? 0})</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">Row</th>
                <th>Source key</th>
                <th>Outcome</th>
                <th>Qualification</th>
                <th>Why (match / rejection reasons)</th>
                <th>Warnings</th>
              </tr>
            </thead>
            <tbody>
              {run.records?.map((r) => (
                <tr key={r.row_number}>
                  <td className="num">{r.row_number}</td>
                  <td className="small mono">
                    {r.company_id ? <Link href={`/companies/${r.company_id}`}>{r.source_key}</Link> : r.source_key}
                  </td>
                  <td>
                    <Badge value={r.outcome} />
                  </td>
                  <td>{r.qualification && <Badge value={r.qualification} />}</td>
                  <td className="small">
                    {r.errors.map((e, i) => (
                      <div key={`e${i}`} className="error">
                        {e}
                      </div>
                    ))}
                    {r.match_reasons.map((m, i) => (
                      <div key={`m${i}`}>{m}</div>
                    ))}
                  </td>
                  <td className="small muted">
                    {r.warnings.map((w, i) => (
                      <div key={i}>{w}</div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
