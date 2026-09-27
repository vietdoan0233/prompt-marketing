// Small display helpers shared by the ingestion run list and run detail pages.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "27 Sep 2026" and "08:42" in UTC (the API stores UTC timestamps). */
export function fmtDay(iso: string | null | undefined): { date: string; time: string } | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    date: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
    time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`,
  };
}

/** "27 Sep 2026 · 08:42 UTC", or "—" when missing. */
export function fmtStamp(iso: string | null | undefined): string {
  const d = fmtDay(iso);
  return d ? `${d.date} · ${d.time} UTC` : "—";
}

/** Integer with thousands separators; "—" when the value was not recorded (absence is never shown as 0). */
export function fmtInt(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-US") : "—";
}

/** Shortened hash for tables ("5c17b86f…4b43"); the caller keeps the full value in a title. */
export function shortHash(h: string | null | undefined): string {
  if (!h) return "—";
  return h.length > 16 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
}

function yearsText(v: unknown): string | null {
  if (!Array.isArray(v) || v.length === 0 || !v.every((y) => typeof y === "number")) return null;
  const ys = [...(v as number[])].sort((a, b) => a - b);
  const contiguous = ys.every((y, i) => i === 0 || y === ys[i - 1] + 1);
  if (ys.length === 1) return `year ${ys[0]}`;
  return contiguous ? `years ${ys[0]}–${ys[ys.length - 1]}` : `years ${ys.join(", ")}`;
}

/** Human summary of a discovery query, e.g. "years 2019–2025". Unknown keys are kept verbatim. */
export function querySummary(query: Record<string, unknown> | null | undefined): string {
  const entries = Object.entries(query ?? {});
  if (entries.length === 0) return "no query parameters";
  return entries
    .map(([k, v]) => (k === "years" ? yearsText(v) : null) ?? `${k.replaceAll("_", " ")} ${JSON.stringify(v)}`)
    .join(" · ");
}

/** Retry needs the raw upload for CSV runs; the API answers 409 "input not retained" otherwise. */
export function retryBlockedReason(run: { kind: string; input_retained: boolean }): string | undefined {
  return run.kind === "csv" && !run.input_retained
    ? "Raw CSV input was not retained (rejected before storage or expired), so this run cannot be retried. Re-upload the file."
    : undefined;
}

// ------------------------------------------------------------------ run counters
// Every counter the run persisted is shown, grouped plainly. Dividend and capex counters are never shown
// (CLAUDE.md: no current row populates either field).
const HIDDEN_COUNTER = /dividend|capex/i;

type CounterGroup = { title: string; keys?: string[]; prefix?: string; note?: string };

const COUNTER_GROUPS: CounterGroup[] = [
  { title: "Qualification", keys: ["qualified", "borderline", "below_threshold", "sub_scale", "unknown_headcount"] },
  {
    title: "Facts, contacts and identity",
    keys: ["facts_added", "facts_changed", "contacts_added", "contacts_suppressed", "duplicate_candidates", "warnings"],
  },
  {
    title: "Annual reports",
    keys: ["reports_imported", "orphan_reports"],
    note: "Orphan reports are indicator reports with no matching general-info row; their values were not imported and each is also counted as rejected.",
  },
  { title: "Financial rows", prefix: "financial_" },
  { title: "Registered addresses", prefix: "address_" },
  { title: "Shareholder sets", prefix: "shareholders_" },
  {
    title: "Financial rows missing a measure",
    prefix: "missing_",
    note: "Imported annual-report rows without a value for the measure. Missing values stay unknown; they are never filled.",
  },
];

const ACRONYMS: Record<string, string> = { ebitda: "EBITDA", fte: "FTE" };
const counterLabel = (k: string) =>
  k
    .split("_")
    .map((w) => ACRONYMS[w] ?? w)
    .join(" ");

export type CounterSection = { title: string; note?: string; items: { key: string; label: string; value: number }[] };

/** Groups the counters present on a run (excluding `exclude` and hidden keys); keys not in a group go to "Other". */
export function counterSections(counts: Record<string, number>, exclude: Set<string>): CounterSection[] {
  const used = new Set<string>();
  const present = Object.keys(counts).filter((k) => !exclude.has(k) && !HIDDEN_COUNTER.test(k));
  const sections: CounterSection[] = [];
  for (const g of COUNTER_GROUPS) {
    const keys = g.keys ? g.keys.filter((k) => present.includes(k)) : present.filter((k) => k.startsWith(g.prefix!));
    if (keys.length === 0) continue;
    keys.forEach((k) => used.add(k));
    sections.push({
      title: g.title,
      note: g.note,
      items: keys.map((k) => ({
        key: k,
        label: counterLabel(g.prefix ? k.slice(g.prefix.length) : k),
        value: counts[k],
      })),
    });
  }
  const rest = present.filter((k) => !used.has(k));
  if (rest.length > 0) {
    sections.push({
      title: "Other counters",
      items: rest.map((k) => ({ key: k, label: counterLabel(k), value: counts[k] })),
    });
  }
  return sections;
}
