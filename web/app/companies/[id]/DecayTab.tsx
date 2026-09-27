import type { ReactNode } from "react";

import { Badge } from "@/components/Badge";
import { EmptyState, Panel, Stat, type Tone } from "@/components/ui";
import { fmtDate, fmtMoney } from "@/lib/api";
import type { DigitalDecaySignal, DigitalDecayView } from "@/lib/types";

import styles from "./company.module.css";
import { EvidenceLink, RunLink, fmtCompactMoney } from "./shared";

const VERDICT_LABEL: Record<DigitalDecaySignal["verdict"], string> = {
  coasting: "Coasting",
  decaying: "Decaying",
  watch: "Watch",
  active: "Active",
  insufficient_evidence: "Insufficient evidence",
};

const VERDICT_TONE: Record<DigitalDecaySignal["verdict"], Tone> = {
  coasting: "bad",
  decaying: "warn",
  watch: "warn",
  active: "good",
  insufficient_evidence: "muted",
};

function SourcePolicy() {
  return (
    <div className={styles.policyStrip} aria-label="Source policy">
      <span className={styles.policyTitle}>Source policy</span>
      <span className="mono">web-digital-decay</span>
      <span>opt-in, operator-enabled</span>
      <span>robots.txt respected</span>
      <span>extracted evidence only, no raw HTML</span>
      <span>LinkedIn is not used</span>
    </div>
  );
}

function Explainer() {
  return (
    <p className={styles.explainer}>
      Opt-in website signal (source <span className="mono">web-digital-decay</span>). Footer copyright ≥2 years old,
      news publishing cadence (no post for ≥18 months, or fewer than 3 posts in 18 months — translations counted once,
      bulk re-save dates ignored) and zero open roles on the careers page, read against reported revenue. Verdict{" "}
      <em>watch</em>: website otherwise maintained, but zero open roles and flat or shrinking register headcount (FTE)
      at ≥€5M revenue. Values are estimated heuristics from the company&apos;s own website; register FTE is a filed
      figure. Unknown checks never count as stale. LinkedIn is not used.
    </p>
  );
}

function CheckRow({
  signal,
  kind,
  observed,
  state,
  rule,
  evidence,
  confidence,
}: {
  signal: string;
  kind: string;
  observed: ReactNode;
  state: ReactNode;
  rule: string;
  evidence: ReactNode;
  confidence: ReactNode;
}) {
  return (
    <tr>
      <td>
        <strong>{signal}</strong>
        <span className="cell-sub">{kind}</span>
      </td>
      <td>{observed}</td>
      <td className={styles.ruleCell}>
        {state}
        <span className="cell-sub">{rule}</span>
      </td>
      <td className="small">{evidence}</td>
      <td>{confidence}</td>
    </tr>
  );
}

function DecayResult({ view }: { view: DigitalDecayView }) {
  const s = view.signal;
  const { copyright, news, hiring, last_modified, headcount } = s.checks;
  const hcTone: Tone | undefined =
    headcount?.state === "shrinking" ? "warn" : headcount?.state === "growing" ? "good" : headcount ? undefined : "muted";

  return (
    <>
      <div className="stats">
        <Stat
          value={VERDICT_LABEL[s.verdict] ?? s.verdict}
          label="Verdict"
          tone={VERDICT_TONE[s.verdict] ?? "muted"}
          sub={`${s.stale_count} of ${s.determinable_count} checks are stale`}
        />
        <Stat
          value={s.domain ?? "not found"}
          label="Website domain"
          tone={s.domain ? "accent" : "muted"}
          sub={
            <>
              <Badge
                value={s.domain_verification === "unverified" ? "unknown" : "verified"}
                label={s.domain_verification.replaceAll("_", " ")}
              />{" "}
              domain verification
            </>
          }
        />
        <Stat
          value={s.revenue ? fmtCompactMoney(s.revenue.amount, s.revenue.currency) : "none"}
          label="Revenue used"
          tone={s.revenue ? undefined : "muted"}
          sub={
            s.revenue
              ? `FY${s.revenue.fiscal_year} · ${s.revenue.value_type}`
              : "no reported revenue — cannot be classed as coasting"
          }
        />
        <Stat
          value={
            headcount && headcount.change_pct !== null
              ? `${headcount.change_pct > 0 ? "+" : ""}${headcount.change_pct.toFixed(1)}%`
              : "unknown"
          }
          label="Register FTE trend"
          tone={headcount && headcount.change_pct !== null ? hcTone : "muted"}
          sub={
            headcount
              ? headcount.from_year !== null && headcount.to_year !== null
                ? `FY${headcount.from_year}→FY${headcount.to_year} · ${headcount.state}`
                : headcount.state
              : "no register FTE filed"
          }
        />
      </div>

      <Panel
        title="Evidence by check"
        description="Each check with what was observed, the documented rule it is read against, and the page it was read from."
      >
        <div className="table-wrap">
          <table className={styles.checksTable}>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Observed</th>
                <th>Rule / result</th>
                <th>Evidence</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              <CheckRow
                signal="Footer copyright"
                kind="website check"
                observed={
                  <>
                    <strong>{copyright.year ?? "—"}</strong>
                    {copyright.age_years !== null && <span className="cell-sub">{copyright.age_years} y old</span>}
                  </>
                }
                state={<Badge value={copyright.state} />}
                rule="Stale when the footer copyright year is ≥2 years old."
                evidence={<EvidenceLink url={copyright.evidence_url} />}
                confidence={<Badge value={view.confidence} />}
              />
              <CheckRow
                signal="News / press cadence"
                kind="website check"
                observed={
                  <>
                    <strong>{news.latest_date ?? "—"}</strong>
                    {news.age_months !== null && <span className="cell-sub">latest post {news.age_months} months ago</span>}
                    {news.posts_18m != null && (
                      <span className="cell-sub">
                        {news.posts_18m} post{news.posts_18m === 1 ? "" : "s"} in 18 months
                        {news.cadence_source &&
                          ` (from ${news.cadence_source === "sitemap" ? "sitemap" : "news page"})`}
                      </span>
                    )}
                    {news.method && <span className="cell-sub mono">via {news.method}</span>}
                    {news.reason && (
                      <span className="cell-sub tone-text-warn">
                        stale because:{" "}
                        {news.reason === "low_cadence" ? "fewer than 3 posts in 18 months" : "no post in 18 months"}
                      </span>
                    )}
                    {news.post_dates && news.post_dates.length > 0 && (
                      <span className="cell-sub mono">{news.post_dates.join(" · ")}</span>
                    )}
                  </>
                }
                state={<Badge value={news.state} />}
                rule="Stale when there is no post for ≥18 months, or fewer than 3 posts in 18 months. Translations counted once; bulk re-save dates ignored; on-page dates beat sitemap lastmod."
                evidence={<EvidenceLink url={news.evidence_url} />}
                confidence={<Badge value={view.confidence} />}
              />
              <CheckRow
                signal="Open roles (hiring)"
                kind="website check"
                observed={
                  <>
                    <strong>
                      {hiring.open_roles !== null ? `${hiring.open_roles} open roles` : "open roles unknown"}
                    </strong>
                    {hiring.ats && <span className="cell-sub">ATS {hiring.ats}</span>}
                  </>
                }
                state={<Badge value={hiring.state} />}
                rule="Counts as stale when the careers page lists zero open roles."
                evidence={<EvidenceLink url={hiring.careers_url} label="careers page" />}
                confidence={<Badge value={view.confidence} />}
              />
              <CheckRow
                signal="Headcount · register"
                kind="register FTE, filed figure"
                observed={
                  headcount ? (
                    <>
                      <strong>
                        {headcount.change_pct !== null
                          ? `${headcount.change_pct > 0 ? "+" : ""}${headcount.change_pct.toFixed(1)}%`
                          : "change unknown"}
                      </strong>
                      {headcount.from_year !== null && headcount.to_year !== null && (
                        <span className="cell-sub">
                          FY{headcount.from_year}→FY{headcount.to_year}
                        </span>
                      )}
                      {headcount.series.length > 0 && (
                        <span className="cell-sub mono">
                          {headcount.series.map(([year, fte]) => `${year}: ${fte}`).join(" · ")}
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="muted">no register FTE filed</span>
                  )
                }
                state={headcount ? <Badge value={headcount.state} /> : <Badge value="unknown" />}
                rule="Context only, not a website check: flat or shrinking register FTE feeds the watch verdict."
                evidence={<EvidenceLink url={null} />}
                confidence={<span className="small muted">register filing</span>}
              />
              <CheckRow
                signal="Last-Modified header"
                kind="HTTP header"
                observed={
                  <strong>{last_modified.header ? fmtDate(last_modified.header) : "not sent"}</strong>
                }
                state={<Badge value="recorded" label="recorded" tone="muted" />}
                rule="Recorded as context; not counted in the stale checks."
                evidence={<EvidenceLink url={last_modified.url} label="page" />}
                confidence={<Badge value={view.confidence} />}
              />
              <CheckRow
                signal="Revenue · register"
                kind="annual report figure"
                observed={
                  s.revenue ? (
                    <>
                      <strong>{fmtMoney(s.revenue.amount, s.revenue.currency)}</strong>
                      <span className="cell-sub">
                        FY{s.revenue.fiscal_year} · {s.revenue.value_type}
                      </span>
                    </>
                  ) : (
                    <span className="muted">no reported revenue — cannot be classed as coasting</span>
                  )
                }
                state={
                  s.revenue ? (
                    <Badge value="threshold" label="threshold input" tone="muted" />
                  ) : (
                    <Badge value="unknown" />
                  )
                }
                rule="Read only for verdict thresholds: missing revenue prevents a coasting or watch verdict."
                evidence={<EvidenceLink url={null} />}
                confidence={<span className="small muted">{s.revenue ? s.revenue.value_type : "—"}</span>}
              />
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid grid-2">
        <Panel title="Provenance" description="Where this signal came from and how it was recorded.">
          <dl className={`kv ${styles.kv}`}>
            <dt>Source</dt>
            <dd className="mono">{view.source_id}</dd>
            {view.source_url && (
              <>
                <dt>Source URL</dt>
                <dd>
                  <a href={view.source_url} target="_blank" rel="noreferrer" className={styles.breakAll}>
                    {view.source_url}
                  </a>
                </dd>
              </>
            )}
            <dt>Observed</dt>
            <dd>{fmtDate(view.observed_at, true)}</dd>
            <dt>Confidence</dt>
            <dd>
              <Badge value={view.confidence} />
            </dd>
            <dt>Review</dt>
            <dd>
              <Badge value={view.review_status} />
            </dd>
            <dt>Ingestion run</dt>
            <dd>{view.ingestion_run_id ? <RunLink runId={view.ingestion_run_id} /> : "—"}</dd>
            <dt>Parser version</dt>
            <dd className="mono">{s.version}</dd>
          </dl>
        </Panel>
        <Panel title="Warnings" description="Notes recorded by the check itself.">
          {s.warnings.length > 0 ? (
            <ul className="dot-list warn">
              {s.warnings.map((w) => (
                <li key={w} className="small">
                  {w}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>No warnings were recorded for this check.</EmptyState>
          )}
        </Panel>
      </div>
    </>
  );
}

export function DecayTab({ view }: { view: DigitalDecayView | null | undefined }) {
  return (
    <>
      <Panel title="Digital decay (operational stagnation)">
        <Explainer />
        <SourcePolicy />
      </Panel>
      {view?.signal ? (
        <DecayResult view={view} />
      ) : (
        <Panel>
          <EmptyState>No website check result is recorded for this company.</EmptyState>
        </Panel>
      )}
    </>
  );
}
