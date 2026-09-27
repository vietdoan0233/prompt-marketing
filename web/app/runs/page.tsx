import Link from "next/link";

import { ActionButton } from "@/components/ActionButton";
import { Badge, Count } from "@/components/Badge";
import { apiGet, fmtDate } from "@/lib/api";
import type { IngestionRun, Source } from "@/lib/types";

import { RunForms } from "./RunForms";

export const dynamic = "force-dynamic";

export default async function RunsPage() {
  const [runs, sources] = await Promise.all([
    apiGet<IngestionRun[]>("/ingestion-runs", { limit: 200 }),
    apiGet<Source[]>("/sources"),
  ]);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Ingestion runs</h1>
          <p className="subtitle">
            IMPORT_STARTED → FETCHED → PARSED → VALIDATED → UPSERTED, or REJECTED at the permission gate. Reruns of the
            same snapshot are idempotent (source key + content hash).
          </p>
        </div>
      </div>
      <RunForms sources={sources} />
      <section className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Started</th>
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
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td className="small">
                    <Link href={`/runs/${r.id}`}>{fmtDate(r.started_at, true)}</Link>
                    {r.retry_of_id && <div className="muted">retry</div>}
                  </td>
                  <td className="small">
                    <code>{r.source_id}</code>
                  </td>
                  <td className="small">
                    {r.kind === "csv" ? r.file_name : <code>{JSON.stringify(r.query)}</code>}
                    <div className="muted mono">{r.input_hash?.slice(0, 12)}</div>
                  </td>
                  <td>
                    <Badge value={r.status} />
                    {r.errors.length > 0 && (
                      <div className="small error" title={r.errors.map((e) => e.message).join("\n")}>
                        {r.errors[0].message.slice(0, 70)}
                      </div>
                    )}
                  </td>
                  <td className="num">{r.counts.discovered ?? 0}</td>
                  <td className="num">
                    <Count n={r.counts.accepted ?? 0} tone="good" />
                  </td>
                  <td className="num">{r.counts.updated ?? 0}</td>
                  <td className="num">{r.counts.unchanged ?? 0}</td>
                  <td className="num">
                    <Count n={r.counts.duplicates ?? 0} tone="warn" />
                  </td>
                  <td className="num">
                    <Count n={r.counts.rejected ?? 0} tone="bad" />
                  </td>
                  <td className="num">{r.counts.sub_scale ?? 0}</td>
                  <td className="num">{r.counts.warnings ?? 0}</td>
                  <td>
                    <ActionButton
                      path={`/ingestion-runs/${r.id}/retry`}
                      label="Retry"
                      openRun
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {runs.length === 0 && <p className="muted">No runs yet. Import a CSV or run discovery above.</p>}
      </section>
    </>
  );
}
