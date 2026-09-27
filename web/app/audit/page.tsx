import Link from "next/link";
import { Fragment } from "react";

import { EmptyState, PageHeader, Panel } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

import styles from "./audit.module.css";

export const dynamic = "force-dynamic";

// Page size for the audit history. The API returns no total, so one extra event is requested to detect "older".
const PAGE_SIZE = 100;

const ACTION_PREFIXES = [
  "source.",
  "ingestion.",
  "fact.",
  "identity.",
  "company.",
  "contact.",
  "retention.",
  "digital_decay.",
  "maintenance.",
];

// Every entity_type the API records (audit.record calls in api/app/routers and api/app/services).
const ENTITY_TYPES = [
  "company",
  "company_fact",
  "contact",
  "database",
  "duplicate_candidate",
  "ingestion_run",
  "retention",
  "source",
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Formats an ISO 8601 timestamp as a UTC date and time without relying on the server locale. */
function utcParts(iso: string): { date: string; time: string } {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return { date: iso, time: "" };
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    date: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
    time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`,
  };
}

function isPrimitive(v: unknown): v is string | number | boolean | null {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return v;
  if (isPrimitive(v)) return String(v);
  if (Array.isArray(v) && v.every((x) => typeof x === "string" || typeof x === "number")) return v.join(", ");
  return compactJson(v);
}

/** Single-line JSON with a space after each separator, so long nested values wrap between entries. */
function compactJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(compactJson).join(", ")}]`;
  if (v && typeof v === "object") {
    return `{${Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${JSON.stringify(k)}: ${compactJson(x)}`)
      .join(", ")}}`;
  }
  return JSON.stringify(v) ?? String(v);
}

function clip(s: string, max = 48): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Short sub-line under the action: the first two scalar (or scalar-list) values in the details. */
function summarize(details: Record<string, unknown> | null | undefined): string {
  if (!details) return "";
  const parts: string[] = [];
  for (const [key, value] of Object.entries(details)) {
    if (parts.length >= 2) break;
    if (typeof value === "string" || typeof value === "number") {
      parts.push(clip(String(value)));
    } else if (typeof value === "boolean") {
      parts.push(`${key}: ${value}`);
    } else if (Array.isArray(value) && value.length && value.every((x) => typeof x === "string" || typeof x === "number")) {
      parts.push(clip(value.join(", ")));
    }
  }
  return parts.join(" · ");
}

function Details({ details }: { details: Record<string, unknown> | null | undefined }) {
  const entries = details ? Object.entries(details) : [];
  if (!entries.length) return <span className="faint">—</span>;
  return (
    <>
      {entries.map(([key, value], i) => (
        <Fragment key={key}>
          {i > 0 && (
            <span className={styles.detailSep} aria-hidden="true">
              ·
            </span>
          )}
          <span>
            <span className={styles.detailKey}>{key}:</span> {formatValue(value)}
          </span>
        </Fragment>
      ))}
    </>
  );
}

export default async function AuditPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const parsedOffset = Number.parseInt(sp.offset ?? "0", 10);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? parsedOffset : 0;
  const fetched = await apiGet<AuditEvent[]>("/audit-events", {
    action: sp.action,
    entity_type: sp.entity_type,
    limit: PAGE_SIZE + 1,
    offset,
  });
  const hasOlder = fetched.length > PAGE_SIZE;
  const events = hasOlder ? fetched.slice(0, PAGE_SIZE) : fetched;
  const hasNewer = offset > 0;
  const pageHref = (o: number) => {
    const q = new URLSearchParams();
    if (sp.action) q.set("action", sp.action);
    if (sp.entity_type) q.set("entity_type", sp.entity_type);
    if (o > 0) q.set("offset", String(o));
    const s = q.toString();
    return `/audit${s ? `?${s}` : ""}`;
  };
  const range =
    events.length > 0
      ? `Showing events ${(offset + 1).toLocaleString("en-US")}–${(offset + events.length).toLocaleString("en-US")}, newest first`
      : null;
  const actionOptions =
    sp.action && !ACTION_PREFIXES.includes(sp.action) ? [...ACTION_PREFIXES, sp.action] : ACTION_PREFIXES;
  const entityOptions =
    sp.entity_type && !ENTITY_TYPES.includes(sp.entity_type) ? [...ENTITY_TYPES, sp.entity_type] : ENTITY_TYPES;
  const filtered = Boolean(sp.action || sp.entity_type);

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Data governance" }, { label: "Audit history" }]}
        title="Audit log"
        subtitle="Every permission change, ingestion, correction, identity decision, retention expiry and GDPR erasure, with actor and timestamp. Audit details never contain personal contact data."
      />

      <form className={`panel filters ${styles.filterBar}`} method="get">
        <h2 className="filters-title">Filter audit events</h2>
        <label className={styles.field}>
          Action prefix
          <select name="action" defaultValue={sp.action ?? ""}>
            <option value="">All actions</option>
            {actionOptions.map((p) => (
              <option key={p} value={p}>
                {p.endsWith(".") ? `${p}*` : p}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          Entity type
          <select name="entity_type" defaultValue={sp.entity_type ?? ""}>
            <option value="">All entities</option>
            {entityOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <div className={styles.actions}>
          <button className="btn btn-primary">Filter</button>
          <Link href="/audit" className="btn btn-ghost">
            Reset
          </Link>
        </div>
        <p className={styles.note}>
          {range ?? "No events on this page"} · {PAGE_SIZE} per page
        </p>
      </form>

      <Panel
        title="Recent activity"
        description="Audit details exclude personal contact data. Company and ingestion-run IDs link back to their records."
      >
        {events.length === 0 ? (
          <EmptyState>
            {offset > 0 ? (
              <>
                No events at this position. <Link href={pageHref(0)}>Go to the newest events</Link>.
              </>
            ) : filtered ? (
              "No audit events match these filters."
            ) : (
              "No audit events have been recorded yet."
            )}
          </EmptyState>
        ) : (
          <>
            <div className="table-wrap">
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Actor</th>
                    <th>Action</th>
                    <th>Entity type</th>
                    <th>Reference</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((e) => {
                    const when = utcParts(e.occurred_at);
                    const summary = summarize(e.details);
                    return (
                      <tr key={e.id}>
                        <td className={styles.when}>
                          <strong>{when.date}</strong>
                          {when.time && <span className="sub">{when.time}</span>}
                        </td>
                        <td className={styles.actor}>{e.actor}</td>
                        <td className={styles.action}>
                          <span className={`mono ${styles.actionName}`}>{e.action}</span>
                          {summary && <span className="sub">{summary}</span>}
                        </td>
                        <td className={styles.entityType}>{e.entity_type}</td>
                        <td className={styles.reference}>
                          {e.company_id ? (
                            <Link href={`/companies/${e.company_id}`} className="mono">
                              {e.company_id.slice(0, 8)}
                            </Link>
                          ) : e.entity_type === "ingestion_run" && e.entity_id ? (
                            <Link href={`/runs/${e.entity_id}`} className="mono">
                              {e.entity_id.slice(0, 8)}
                            </Link>
                          ) : e.entity_id ? (
                            <span className="mono muted">{e.entity_id.slice(0, 24)}</span>
                          ) : (
                            <span className="faint">—</span>
                          )}
                        </td>
                        <td className={styles.details}>
                          <Details details={e.details} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {(hasNewer || hasOlder) && (
              <nav className={`pagination ${styles.pager}`} aria-label="Audit history pages">
                {hasNewer ? (
                  <Link className="btn btn-sm" href={pageHref(Math.max(0, offset - PAGE_SIZE))} rel="prev">
                    ← Newer
                  </Link>
                ) : (
                  <span className={`btn btn-sm ${styles.pageOff}`} aria-disabled="true">
                    ← Newer
                  </span>
                )}
                <span className="muted small">{range}</span>
                {hasOlder ? (
                  <Link className="btn btn-sm" href={pageHref(offset + PAGE_SIZE)} rel="next">
                    Older →
                  </Link>
                ) : (
                  <span className={`btn btn-sm ${styles.pageOff}`} aria-disabled="true">
                    Older →
                  </span>
                )}
              </nav>
            )}
          </>
        )}
      </Panel>
    </>
  );
}
