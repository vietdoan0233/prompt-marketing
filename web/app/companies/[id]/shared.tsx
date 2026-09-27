// Small presentational helpers shared by the company-detail tabs. Server-safe (no hooks).

import Link from "next/link";

import { Badge } from "@/components/Badge";
import { fmtDate, fmtMoney, fmtValue } from "@/lib/api";
import type { Fact, Financial } from "@/lib/types";

import styles from "./company.module.css";

export const TAB_KEYS = ["profile", "ownership", "decay", "review", "contacts"] as const;
export type TabKey = (typeof TAB_KEYS)[number];

/** Money / count values arrive as decimal strings from the API; convert without guessing. */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** "34.97M EUR" style figure for stat cards and chart labels; the full value stays in tables. */
export function fmtCompactMoney(v: unknown, currency = "EUR"): string {
  const n = num(v);
  if (n === null) return "—";
  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
  return `${compact} ${currency}`;
}

/** Audit / timeline detail objects rendered as `key: value · key: value`, keeping every key. */
export function fmtDetails(details: Record<string, unknown> | null | undefined): string {
  const entries = Object.entries(details ?? {});
  if (entries.length === 0) return "—";
  return entries
    .map(([k, v]) => {
      let s: string;
      if (v === null || v === undefined) s = "—";
      else if (Array.isArray(v)) s = v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ") : "none";
      else if (typeof v === "object") s = JSON.stringify(v);
      else s = String(v);
      return `${k.replaceAll("_", " ")}: ${s}`;
    })
    .join(" · ");
}

export function EvidenceLink({ url, label }: { url: string | null | undefined; label?: string }) {
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className={styles.evidenceLink}>
      {label ?? "evidence"} ↗
    </a>
  ) : (
    <span className="small muted">no evidence URL</span>
  );
}

export function RunLink({ runId, className }: { runId: string | null | undefined; className?: string }) {
  if (!runId) return null;
  return (
    <Link href={`/runs/${runId}`} className={className}>
      run {runId.slice(0, 8)}
    </Link>
  );
}

/** One source-backed fact with full provenance. Superseded rows render muted. */
export function FactRow({ f }: { f: Fact }) {
  return (
    <tr className={f.valid_to ? "row-muted" : ""}>
      <td className="mono small">{f.field_name}</td>
      <td>
        <span className={styles.factValue}>{fmtValue(f.value_json)}</span>
        {f.original_value && f.original_value !== fmtValue(f.value_json) && (
          <span className="cell-sub">source value: “{f.original_value}”</span>
        )}
        {f.code_system && (
          <span className="cell-sub">
            {f.code_system} {f.code_version ?? "version unknown"}
          </span>
        )}
        {f.correction_reason && <span className="cell-sub tone-text-info">reason: {f.correction_reason}</span>}
      </td>
      <td className="small">
        {f.source_name ?? f.source_id}
        <span className="cell-sub mono">{f.source_id}</span>
      </td>
      <td className="small">
        {f.source_url ? (
          <a href={f.source_url} target="_blank" rel="noreferrer" className={styles.breakAll}>
            {f.source_key ?? "link"}
          </a>
        ) : (
          <span className={`mono ${styles.breakAll}`}>{f.source_key ?? "—"}</span>
        )}
        {f.ingestion_run_id && (
          <span className="cell-sub">
            <RunLink runId={f.ingestion_run_id} className="muted" />
          </span>
        )}
      </td>
      <td className="small">{fmtDate(f.observed_at)}</td>
      <td>
        <Badge value={f.confidence} />
      </td>
      <td className="small">
        <Badge value={f.review_status === "corrected" ? "manually-corrected" : f.review_status} label={f.review_status} />
        <span className="cell-sub">{f.usage_policy}</span>
        {f.valid_to && <span className="cell-sub">superseded {fmtDate(f.valid_to)}</span>}
      </td>
    </tr>
  );
}

export function FactTableHead() {
  return (
    <thead>
      <tr>
        <th>Field</th>
        <th>Value</th>
        <th>Source</th>
        <th>Reference</th>
        <th>Observed</th>
        <th>Confidence</th>
        <th>Review / policy</th>
      </tr>
    </thead>
  );
}

/**
 * Inline revenue sparkline from real financial rows. Uses one statement scope only (the most common among rows
 * with revenue) so standalone and consolidated figures are never mixed; values are plotted as filed, not annualized.
 * Returns null unless at least two fiscal years have revenue.
 */
export function RevenueTrend({ rows }: { rows: Financial[] }) {
  const withRevenue = rows.filter((r) => num(r.revenue) !== null);
  if (withRevenue.length < 2) return null;
  const scopeCounts = new Map<string, number>();
  for (const r of withRevenue) {
    const k = r.statement_scope ?? "scope unknown";
    scopeCounts.set(k, (scopeCounts.get(k) ?? 0) + 1);
  }
  const scope = [...scopeCounts.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const byYear = new Map<number, Financial>();
  for (const r of withRevenue) {
    if ((r.statement_scope ?? "scope unknown") !== scope) continue;
    const prev = byYear.get(r.fiscal_year);
    // Prefer the most recently observed filing for a year.
    if (!prev || r.observed_at > prev.observed_at) byYear.set(r.fiscal_year, r);
  }
  const points = [...byYear.values()].sort((a, b) => a.fiscal_year - b.fiscal_year);
  if (points.length < 2) return null;

  const values = points.map((p) => num(p.revenue) as number);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const W = 260;
  const H = 64;
  const padX = 6;
  const padY = 8;
  const x = (i: number) => padX + (i * (W - 2 * padX)) / (points.length - 1);
  const y = (v: number) => (max === min ? H / 2 : padY + ((max - v) * (H - 2 * padY)) / (max - min));
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(values[i]).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const currency = last.currency ?? "EUR";
  const summary = points.map((p, i) => `FY${p.fiscal_year} ${fmtCompactMoney(values[i], p.currency ?? "EUR")}`).join(", ");

  return (
    <figure className={styles.trend} aria-label={`Revenue by fiscal year (${scope}): ${summary}`}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" className={styles.trendSvg}>
        <title>{`Revenue by fiscal year (${scope}): ${summary}`}</title>
        <line x1={padX} x2={W - padX} y1={H - 1} y2={H - 1} className={styles.trendAxis} />
        <path d={path} className={styles.trendLine} />
        {points.map((p, i) => (
          <circle
            key={p.id}
            cx={x(i)}
            cy={y(values[i])}
            r={i === points.length - 1 ? 3.6 : 2.6}
            className={i === points.length - 1 ? styles.trendDotLast : styles.trendDot}
          />
        ))}
      </svg>
      <figcaption className={styles.trendCaption}>
        <span className={styles.trendValue}>{fmtCompactMoney(values[values.length - 1], currency)}</span>
        <span className="small muted">
          revenue FY{last.fiscal_year} · {scope}
        </span>
        <span className="small faint">
          FY{points[0].fiscal_year}–FY{last.fiscal_year} · as filed, not annualized · {fmtMoney(values[values.length - 1], currency)}
        </span>
      </figcaption>
    </figure>
  );
}
