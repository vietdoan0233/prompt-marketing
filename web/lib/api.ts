// Server-side fetch helper for React Server Components. Browser code uses /api/* (see next.config.mjs).

import { notFound } from "next/navigation";

const API_URL = process.env.API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

type Params = Record<string, string | number | undefined | null>;

export async function apiGet<T>(path: string, params?: Params): Promise<T> {
  const url = new URL(path, API_URL);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch {
    // Network failure: the API process is down or unreachable. Distinct from an HTTP error response.
    throw new ApiError(503, `The company database API is not reachable (${url.pathname}).`);
  }
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

/** Like apiGet, but a 404 renders the route's not-found state instead of the error boundary. */
export async function apiGetOrNotFound<T>(path: string, params?: Params): Promise<T> {
  try {
    return await apiGet<T>(path, params);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
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
    if ("amount" in o) {
      return fmtMoney(o.amount as number | string | null, (o.currency as string) ?? "EUR");
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
