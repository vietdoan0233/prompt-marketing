import Link from "next/link";

import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader, Panel } from "@/components/ui";
import { fmtDate, fmtEmployees } from "@/lib/api";
import type { CompanyDetail, Duplicate } from "@/lib/types";

import { DuplicateActions } from "./DuplicateActions";
import { effectKind, effectTone, humanise, registryIdsDisagree } from "./format";
import styles from "./quality.module.css";

export const REVIEW_CRUMBS = [{ label: "Data quality", href: "/quality" }, { label: "Reviewer decision" }];
export const REVIEW_TITLE = "Duplicate candidate review";
export const REVIEW_SUBTITLE =
  "Possible identity match · compare evidence before choosing whether to merge, link as related, or dismiss.";

/** Latest filed FTE from annual-report financials (value may arrive as a decimal string). */
function latestFte(detail: CompanyDetail): string {
  const rows = [...(detail.financials ?? [])]
    .filter((f) => f.employees_fte !== null && f.employees_fte !== undefined && Number.isFinite(Number(f.employees_fte)))
    .sort((a, b) => b.fiscal_year - a.fiscal_year);
  if (rows.length > 0) {
    const n = Number(rows[0].employees_fte);
    return `${n.toLocaleString("en", { maximumFractionDigits: 2 })} · FY${rows[0].fiscal_year}`;
  }
  const c = detail.company;
  if (c.estimated_employee_min !== null) {
    return `${fmtEmployees(c.estimated_employee_min, c.estimated_employee_max)} (headcount field, no filed FTE)`;
  }
  return "—";
}

function address(detail: CompanyDetail): string {
  const a = detail.registered_address;
  const parts = a ? [a.address_line, a.city] : [detail.company.city];
  const text = parts.filter((p): p is string => Boolean(p)).join(" · ");
  return text || "—";
}

function CompanyCard({
  role,
  id,
  name,
  detail,
}: {
  role: string;
  id: string;
  name: string | null;
  detail: CompanyDetail | null;
}) {
  const c = detail?.company;
  return (
    <Panel>
      <Link href={`/companies/${id}`} className={styles.cardName}>
        {name ?? c?.legal_name ?? role}
      </Link>
      <p className={styles.role}>{role}</p>
      {!detail || !c ? (
        <p className="notice notice-bad" role="alert">
          This company record could not be loaded. Open the <Link href={`/companies/${id}`}>company page</Link> to
          check it directly.
        </p>
      ) : (
        <>
          <div className={styles.cardBadges}>
            {c.registry_status ? (
              <Badge value={c.registry_status} title="Registry status as reported by the source" />
            ) : (
              <Badge value="unknown" label="registry status unknown" />
            )}
          </div>
          <dl className="kv">
            <dt>Registry ID</dt>
            <dd className="mono">{c.registry_id ?? "—"}</dd>
            <dt>Registered address</dt>
            <dd>{address(detail)}</dd>
            <dt>Website</dt>
            <dd>{c.website ?? "—"}</dd>
            <dt>Latest reported FTE</dt>
            <dd>{latestFte(detail)}</dd>
            <dt>Qualification</dt>
            <dd>
              <Badge value={c.qualification_status} />
            </dd>
            <dt>Sources</dt>
            <dd>
              {c.source_count}
              {c.source_ids.length > 0 && <span className="muted"> · {c.source_ids.join(", ")}</span>}
            </dd>
            <dt>Review status</dt>
            <dd>
              <Badge value={c.review_status} />
            </dd>
            {detail.merged_into_id && (
              <>
                <dt>Merged into</dt>
                <dd>
                  <Link href={`/companies/${detail.merged_into_id}`} className="mono">
                    {detail.merged_into_id.slice(0, 8)}
                  </Link>
                </dd>
              </>
            )}
          </dl>
        </>
      )}
    </Panel>
  );
}

/** Side-by-side comparison, match signals and reviewer decision for one candidate. `a`/`b` are null when a
 * company record could not be loaded. */
export function DuplicateReview({
  d,
  a,
  b,
}: {
  d: Duplicate;
  a: CompanyDetail | null;
  b: CompanyDetail | null;
}) {
  const blocked = registryIdsDisagree(d);
  const isOpen = d.status === "open";
  const missing = [!a && "Company A", !b && "Company B"].filter(Boolean).join(" and ");
  const unavailableReason = missing
    ? `The comparison evidence for ${missing} could not be loaded. Reload the page, or open the company page to check the record, before deciding.`
    : undefined;

  return (
    <>
      <PageHeader
        crumbs={REVIEW_CRUMBS}
        title={REVIEW_TITLE}
        subtitle={REVIEW_SUBTITLE}
        meta={
          <>
            <Badge value={d.band} label={`${d.band} · ${d.score}`} large />
            <Badge value={d.status} large />
            <span className="mono small">Candidate {d.id.slice(0, 8)}</span>
            <span className="dot">·</span>
            <span className="small">Suggested {fmtDate(d.created_at, true)}</span>
          </>
        }
      />

      <div className={styles.pair}>
        <CompanyCard role="Company A" id={d.company_a_id} name={d.company_a_name} detail={a} />
        <span className={styles.vs} aria-hidden="true">
          VS
        </span>
        <CompanyCard role="Company B" id={d.company_b_id} name={d.company_b_name} detail={b} />
      </div>

      <div className={styles.decisionRow}>
        <Panel
          title="Why this match was suggested"
          description="Signals recorded by the duplicate scorer, both for and against the match."
        >
          {d.reasons.length === 0 ? (
            <EmptyState>No signals were recorded for this candidate.</EmptyState>
          ) : (
            <ul className={styles.reasons}>
              {d.reasons.map((r, i) => (
                <li key={i} className={styles[effectKind(r.effect)]}>
                  <span>
                    <Badge value={r.effect} tone={effectTone(r.effect)} />
                  </span>
                  <span className={styles.signal}>{humanise(r.signal)}</span>
                  <span className={styles.detail}>{r.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel
          title="Reviewer decision"
          description={isOpen ? "Record a reason so the outcome is auditable." : "This candidate has been resolved."}
        >
          {isOpen ? (
            <>
              {unavailableReason ? (
                <p className="notice notice-bad">
                  Evidence missing: merge is not offered and link/dismiss are disabled until both company records load.
                </p>
              ) : (
                blocked && <p className="notice">Different registry IDs block merging; link as related or dismiss.</p>
              )}
              <DuplicateActions d={d} stacked unavailableReason={unavailableReason} />
              {!unavailableReason && <p className={styles.hint}>Each action requires a short reason.</p>}
            </>
          ) : (
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <Badge value={d.status} />
              </dd>
              <dt>Resolved by</dt>
              <dd>{d.resolved_by ?? "—"}</dd>
              <dt>Resolved at</dt>
              <dd>{fmtDate(d.resolved_at, true)}</dd>
              <dt>Reason</dt>
              <dd>{d.resolution_reason ?? "—"}</dd>
            </dl>
          )}
          {d.status === "merged" && (
            <p className={styles.hint}>
              The merge moved facts, identifiers and contacts. Financial rows, registered addresses and shareholders
              stayed on the merged-away record; see its page.
            </p>
          )}
        </Panel>
      </div>
    </>
  );
}
