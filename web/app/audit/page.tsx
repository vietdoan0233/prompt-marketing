import Link from "next/link";

import { apiGet, fmtDate } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AuditPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const events = await apiGet<AuditEvent[]>("/audit-events", {
    action: sp.action,
    entity_type: sp.entity_type,
    limit: 300,
  });
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <p className="subtitle">
            Every permission change, ingestion, correction, identity decision, retention expiry and GDPR erasure.
            Audit details never contain personal contact data.
          </p>
        </div>
      </div>
      <form className="panel filters" method="get">
        <label>
          Action prefix
          <select name="action" defaultValue={sp.action ?? ""}>
            <option value="">All</option>
            <option value="source.">source.*</option>
            <option value="ingestion.">ingestion.*</option>
            <option value="fact.">fact.*</option>
            <option value="identity.">identity.*</option>
            <option value="company.">company.*</option>
            <option value="contact.">contact.*</option>
            <option value="retention.">retention.*</option>
          </select>
        </label>
        <button className="btn btn-primary">Filter</button>
      </form>
      <section className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="small">{fmtDate(e.occurred_at, true)}</td>
                  <td className="small">{e.actor}</td>
                  <td className="mono small">{e.action}</td>
                  <td className="small">
                    {e.entity_type}{" "}
                    {e.company_id ? (
                      <Link href={`/companies/${e.company_id}`} className="mono">
                        {e.company_id.slice(0, 8)}
                      </Link>
                    ) : e.entity_type === "ingestion_run" && e.entity_id ? (
                      <Link href={`/runs/${e.entity_id}`} className="mono">
                        {e.entity_id.slice(0, 8)}
                      </Link>
                    ) : (
                      <span className="mono muted">{e.entity_id?.slice(0, 24)}</span>
                    )}
                  </td>
                  <td className="small muted mono" style={{ maxWidth: 520, wordBreak: "break-word" }}>
                    {JSON.stringify(e.details)}
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
