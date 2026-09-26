"use client";

// Browser-side calls go through the Next.js /api rewrite. The reviewer identity is sent as X-Actor
// so every mutation lands in the audit trail (prototype: no SSO yet).

export function getActor(): string {
  try {
    return localStorage.getItem("mergero.actor") || "reviewer@mergero.local";
  } catch {
    return "reviewer@mergero.local";
  }
}

export function setActor(actor: string): void {
  try {
    localStorage.setItem("mergero.actor", actor);
  } catch {
    /* storage unavailable: fall back to default actor */
  }
}

export async function apiSend<T = unknown>(
  method: "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const init: RequestInit = { method, headers: { "X-Actor": getActor() } };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${path}`, init);
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : detail?.reasons
          ? `${detail.message}: ${detail.reasons.join("; ")}`
          : JSON.stringify(detail ?? data);
    throw new Error(msg);
  }
  return data as T;
}
