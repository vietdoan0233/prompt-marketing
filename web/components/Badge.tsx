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
  // seller prospect funnel
  advisor_review: "best",
  research: "warn",
  outside_size_band: "muted",
  exclude: "bad",
  complete: "good",
  needs_data: "warn",
  core: "good",
  adjacent: "muted",
  weak: "bad",
  group_parent: "info",
  holding_activity: "warn",
  cash_harvesting_candidate: "info",
  // digital decay
  coasting: "bad",
  decaying: "warn",
  watch: "warn",
  active: "good",
  insufficient_evidence: "muted",
  zero_roles: "warn",
  hiring: "good",
  growing: "good",
  flat: "warn",
  shrinking: "bad",
};

const LABEL: Record<string, string> = {
  sub_scale: "sub-scale (1–2)",
  below_threshold: "below 20",
  unknown_headcount: "headcount unknown",
  qualified: "≥20 qualified",
  needs_correction: "needs correction",
  advisor_review: "advisor review",
  outside_size_band: "outside size band",
  needs_data: "needs data",
  group_parent: "group parent",
  holding_activity: "holding / head office",
  cash_harvesting_candidate: "Cash Harvesting candidate",
  insufficient_evidence: "insufficient evidence",
  zero_roles: "0 open roles",
  watch: "watch",
};

/** Status pill. Tone comes from the value (TONE map) unless `tone` overrides it: good, best/accent, warn, bad, info, muted. */
export function Badge({
  value,
  title,
  label,
  tone: toneOverride,
  large,
}: {
  value: string;
  title?: string;
  label?: string;
  tone?: string;
  large?: boolean;
}) {
  const tone = toneOverride ?? TONE[value] ?? "muted";
  return (
    <span className={`badge badge-${tone}${large ? " badge-lg" : ""}`} title={title ?? value}>
      {label ?? LABEL[value] ?? value}
    </span>
  );
}

export function Count({ n, tone }: { n: number; tone?: string }) {
  return <span className={`count ${n > 0 && tone ? `count-${tone}` : ""}`}>{n}</span>;
}
