const TONE: Record<string, string> = {
  // confidence / provenance
  "source-verified": "good",
  verified: "good",
  "multi-source": "best",
  estimated: "warn",
  old: "muted",
  stale: "bad",
  conflicting: "bad",
  unknown: "muted",
  "manually-corrected": "info",
  // freshness
  fresh: "good",
  aging: "warn",
  // qualification
  qualified: "good",
  borderline: "warn",
  below_threshold: "muted",
  sub_scale: "bad",
  unknown_headcount: "muted",
  // permission / run status
  approved: "good",
  pending: "warn",
  revoked: "bad",
  unapproved: "bad",
  UPSERTED: "good",
  REJECTED: "bad",
  FAILED: "bad",
  IMPORT_STARTED: "info",
  // record outcomes
  accepted: "good",
  updated: "info",
  unchanged: "muted",
  duplicate: "warn",
  rejected: "bad",
  // review
  reviewed: "good",
  unreviewed: "muted",
  needs_correction: "warn",
  open: "warn",
  merged: "info",
  linked: "info",
  dismissed: "muted",
  likely: "bad",
  possible: "warn",
};

const LABEL: Record<string, string> = {
  sub_scale: "sub-scale (1–2)",
  below_threshold: "below 20",
  unknown_headcount: "headcount unknown",
  qualified: "≥20 qualified",
  needs_correction: "needs correction",
};

export function Badge({ value, title, label }: { value: string; title?: string; label?: string }) {
  const tone = TONE[value] ?? "muted";
  return (
    <span className={`badge badge-${tone}`} title={title ?? value}>
      {label ?? LABEL[value] ?? value}
    </span>
  );
}

export function Count({ n, tone }: { n: number; tone?: string }) {
  return <span className={`count ${n > 0 && tone ? `count-${tone}` : ""}`}>{n}</span>;
}
