// Server-side fetch helper for React Server Components. Browser code uses /api/* (see next.config.mjs).

const API_URL = process.env.API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(path: string, params?: Record<string, string | number | undefined | null>): Promise<T> {
  const url = new URL(path, API_URL);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export function fmtDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime
    ? d.toISOString().replace("T", " ").slice(0, 16) + " UTC"
    : d.toISOString().slice(0, 10);
}

export function fmtEmployees(min: number | null, max: number | null): string {
  if (min === null) return "unknown";
  if (max === null) return `${min}+`;
  return min === max ? String(min) : `${min}–${max}`;
}

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if ("min" in o) {
      const range = fmtEmployees(o.min as number | null, (o.max as number | null) ?? null);
      return o.currency ? `${range} ${o.currency}` : range;
    }
    return JSON.stringify(v);
  }
  return String(v);
}

export function fmtMoney(v: number | string | null | undefined, currency = "EUR"): string {
  if (v === null || v === undefined || v === "") return "—";
  const amount = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(amount)) return String(v);
  return new Intl.NumberFormat("et-EE", {
    style: "currency",
    currency,
    currencyDisplay: "code",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}
