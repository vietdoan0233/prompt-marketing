// Shared parsing and formatting for the seller-prospect funnel and its evidence briefs.
// Margins, ratios and CAGR arrive from the API as fractions (0.153 = 15.3%).

export type SP = Record<string, string | undefined>;

export type ProspectFilters = {
  /** Normalised €m values: exactly what the form shows and what the API receives (× 1,000,000). */
  min: number;
  max: number;
  /** Trimmed, lower-cased EMTAK division code or "others" (the API compares it the same way). */
  sector: string | undefined;
  /** Which list the API returns: "cash_harvesting" (default) filters to that signal; "all" does not. */
  view: "cash_harvesting" | "all";
  /** Opt-in: hides companies whose Digital Decay check came back "active". Off by default. */
  hideActiveDecay: boolean;
  /** One note for every supplied URL value that had to change to become a valid request. */
  adjustments: string[];
};

export const DEFAULT_MIN_MILLIONS = 5;
export const DEFAULT_MAX_MILLIONS = 50;
/** €1,000,000m: far above any Estonian company, and still a plain integer in the request URL. */
const MAX_MILLIONS = 1_000_000;
export const PAGE_SIZE = 50;

export function fmtMillions(value: number): string {
  return `€${value.toLocaleString("en", { maximumFractionDigits: 1 })}m`;
}

/** One revenue bound. Absent or blank → the default, silently; any other invalid value → adjusted, with a note. */
function parseBound(raw: string | undefined, fallback: number, label: string, notes: string[]): number {
  if (raw === undefined || raw.trim() === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    notes.push(`${label} “${raw}” is not a positive number, so ${fmtMillions(fallback)} is used.`);
    return fallback;
  }
  if (value > MAX_MILLIONS) {
    notes.push(`${label} ${raw} is above the ${fmtMillions(MAX_MILLIONS)} ceiling, so ${fmtMillions(MAX_MILLIONS)} is used.`);
    return MAX_MILLIONS;
  }
  // The inputs step in €0.1m, and the API needs a whole, positive euro amount.
  const stepped = Math.max(0.1, Math.round(value * 10) / 10);
  if (stepped !== value) notes.push(`${label} ${raw} is rounded to the €0.1m input step: ${fmtMillions(stepped)}.`);
  return stepped;
}

/** Defaults €5m–€50m; max never below min. Every change to a supplied value is reported in `adjustments`. */
export function parseFilters(sp: SP): ProspectFilters {
  const adjustments: string[] = [];
  const min = parseBound(sp.min_millions, DEFAULT_MIN_MILLIONS, "Min revenue", adjustments);
  let max = parseBound(sp.max_millions, Math.max(min, DEFAULT_MAX_MILLIONS), "Max revenue", adjustments);
  if (max < min) {
    const fixed = Math.max(min, DEFAULT_MAX_MILLIONS);
    adjustments.push(
      `Max revenue ${fmtMillions(max)} is below the minimum ${fmtMillions(min)}, so ${fmtMillions(fixed)} is used.`,
    );
    max = fixed;
  }
  const sector = sp.sector?.trim().toLowerCase() || undefined;
  const view = sp.view === "all" ? "all" : "cash_harvesting";
  const hideActiveDecay = sp.hide_active_decay === "true";
  return { min, max, sector, view, hideActiveDecay, adjustments };
}

/** Whole, non-negative row offset from the URL; anything else means the first page. */
export function parseOffset(sp: SP): number {
  const value = Number(sp.offset ?? 0);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/** Filter context for the API: GET /seller-prospects and GET /seller-prospects/{id} both take these (the
 * brief endpoint does not take view/hide_active_decay; it ignores the extra params). */
export function filterParams(filters: ProspectFilters) {
  return {
    min_revenue_eur: Math.round(filters.min * 1_000_000),
    max_revenue_eur: Math.round(filters.max * 1_000_000),
    sector: filters.sector,
    view: filters.view,
    hide_active_decay: filters.hideActiveDecay ? "true" : undefined,
  };
}

/** Query string carrying the current filters, plus the list offset when it is not the first page. */
export function filterQuery(filters: ProspectFilters, offset = 0): string {
  const q = new URLSearchParams({ min_millions: String(filters.min), max_millions: String(filters.max) });
  if (filters.sector) q.set("sector", filters.sector);
  if (filters.view !== "cash_harvesting") q.set("view", filters.view);
  if (filters.hideActiveDecay) q.set("hide_active_decay", "true");
  if (offset > 0) q.set("offset", String(offset));
  return q.toString();
}

/** "€5m–€50m", for filter summaries. */
export function bandLabel(filters: ProspectFilters): string {
  return `${fmtMillions(filters.min)}–${fmtMillions(filters.max)}`;
}

/** Readable name of a sector filter value when no option label is at hand. */
export function sectorName(code: string): string {
  return code === "others" ? "Others (smaller divisions and companies without a usable code)" : `EMTAK ${code}`;
}

export function money(value: number | null): string {
  if (value === null) return "—";
  return `€${(value / 1_000_000).toFixed(1)}m`;
}

export function percent(value: number | null, digits = 1): string {
  return value === null ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function signedPercent(value: number | null, digits = 1): string {
  if (value === null) return "—";
  const pct = value * 100;
  return `${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(digits)}%`;
}

export function index(value: number | null, digits = 2): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(digits)}`;
}

export function registryCode(registryId: string | null): string | null {
  return registryId ? registryId.replace("EE:", "") : null;
}

/** "2024:3217207" → { year: "2024", id: "3217207" }; an unexpected shape keeps the raw value as the id. */
export function splitFilingId(value: string): { year: string | null; id: string } {
  const at = value.indexOf(":");
  return at > 0 ? { year: value.slice(0, at), id: value.slice(at + 1) } : { year: null, id: value };
}

/** Last path segment of an official dataset URL, e.g. "4.2024_aruannete_elemendid_….zip". */
export function fileName(url: string): string {
  try {
    const parts = new URL(url).pathname.split("/");
    return decodeURIComponent(parts[parts.length - 1] || url);
  } catch {
    return url;
  }
}
