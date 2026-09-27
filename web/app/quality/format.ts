// Small display helpers shared by the data-quality pages. Pure functions only (server-safe).

import type { Duplicate } from "@/lib/types";

export type Reason = Duplicate["reasons"][number];

const WORDS: Record<string, string> = { id: "ID", vat: "VAT", fte: "FTE", emtak: "EMTAK", url: "URL" };

/** "registry_id" -> "Registry ID", "vat_id" -> "VAT ID", "name" -> "Name". */
export function humanise(key: string): string {
  const words = key.split(/[_\s]+/).filter(Boolean);
  return words
    .map((w, i) => WORDS[w.toLowerCase()] ?? (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** CSS-class suffix for a reason effect: for / against (incl. "block") / neutral. */
export function effectKind(effect: string): "for" | "against" | "neutral" {
  if (effect === "for") return "for";
  if (effect === "against" || effect === "block") return "against";
  return "neutral";
}

/** Badge tone for a reason effect. */
export function effectTone(effect: string): "good" | "bad" | "muted" {
  const k = effectKind(effect);
  return k === "for" ? "good" : k === "against" ? "bad" : "muted";
}

/** Different national registry IDs mean different legal entities: the API refuses to merge them. */
export function registryIdsDisagree(d: Duplicate): boolean {
  return d.reasons.some((r) => r.signal === "registry_id" && r.effect === "against");
}

export function fmtCount(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en") : "—";
}
