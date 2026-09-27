import Link from "next/link";
import type { ReactNode } from "react";

import { Badge } from "@/components/Badge";
import { EmptyState, Meter, PageHeader, Panel, Stat } from "@/components/ui";
import { apiGetOrNotFound } from "@/lib/api";
import type { SellerProspect, SellerProspectBrief } from "@/lib/types";

import {
  bandLabel,
  cashHarvestingBasisLabel,
  cashHarvestingBasisSentence,
  fileName,
  filterParams,
  filterQuery,
  index,
  money,
  parseFilters,
  parseOffset,
  percent,
  registryCode,
  sectorName,
  signedPercent,
  splitFilingId,
  type ProspectFilters,
  type SP,
} from "../prospect-format";
import styles from "../prospects.module.css";

export const dynamic = "force-dynamic";

// Cash Harvesting candidate revenue-CAGR band, restated from app.domain.seller_signals for display only.
// The EBITDA-margin threshold is no longer a fixed constant: it depends on the sector-filter comparison
// group (see item.cash_harvesting_margin_threshold, computed per request by app.services.seller_funnel).
const CAGR_MIN = -0.02;
const CAGR_MAX = 0.03;
const CAGR_SCALE = 0.1; // gauge shows −10% … +10%
const EBITDA_SCALE = 0.4; // gauge shows 0% … 40%

export default async function ProspectBriefPage({
  params,
  searchParams,
}: {
  params: Promise<{ companyId: string }>;
  searchParams: Promise<SP>;
}) {
  const [{ companyId }, sp] = await Promise.all([params, searchParams]);
  const filters = parseFilters(sp);
  const offset = parseOffset(sp);
  // One company under the same filter context as the list; a 404 renders the shared not-found state.
  const brief = await apiGetOrNotFound<SellerProspectBrief>(
    `/seller-prospects/${encodeURIComponent(companyId)}`,
    filterParams(filters),
  );
  const { item } = brief;
  const funnelHref = `/seller-prospects?${filterQuery(filters, offset)}`;
  const code = registryCode(item.registry_id);
  const position =
    brief.rank !== null
      ? `#${brief.rank.toLocaleString("en")} of ${brief.total_items.toLocaleString("en")} in this filter`
      : null;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Seller prospects", href: funnelHref }, { label: "Evidence brief" }]}
        title={item.legal_name}
        subtitle={
          <>
            Evidence brief under the list filter: revenue band {bandLabel(filters)} ·{" "}
            {filters.sector ? sectorName(filters.sector) : "all sectors"}
          </>
        }
        meta={
          <>
            <Badge value={item.next_action} large />
            {item.flags.map((flag) => (
              <Badge key={flag} value={flag} large />
            ))}
            <Badge value={item.evidence_status} large title="Financial evidence status" />
            {item.digital_decay_verdict && (
              <Badge value={item.digital_decay_verdict} large title="Website signal (digital decay)" />
            )}
            <span className={styles.metaCode}>{code ? `EE: ${code}` : "No registry ID"}</span>
            {position && <span className={styles.metaCode}>{position}</span>}
          </>
        }
        actions={
          <div className={styles.headActions}>
            <Link className="btn btn-ghost" href={`${funnelHref}#prospects`}>
              ‹ Back to the list
            </Link>
            {item.registry_url && (
              <a className="btn" href={item.registry_url} target="_blank" rel="noreferrer">
                Register card ↗
              </a>
            )}
            <Link className="btn btn-primary" href={`/companies/${item.company_id}`}>
              Open company profile
            </Link>
          </div>
        }
      />

      <FilterContext brief={brief} filters={filters} />

      <div className="stats">
        <Stat
          value={money(item.latest_revenue_eur)}
          label="Latest reported revenue"
          sub={item.latest_year ? `FY${item.latest_year} · standalone · EUR` : "No comparable standalone EUR report"}
        />
        <Stat
          value={item.positive_profit_years === null ? "—" : `${item.positive_profit_years} / 3`}
          label="Positive profit years"
          tone={item.positive_profit_years === 3 ? "good" : undefined}
          sub={
            item.positive_profit_years === null
              ? "Three consecutive comparable years unavailable"
              : "Operating profit, consecutive comparable reports"
          }
        />
        <Stat
          value={percent(item.three_year_median_margin)}
          label="Median operating margin"
          sub={
            item.three_year_median_margin === null
              ? "Three consecutive comparable years unavailable"
              : "Three comparable fiscal years"
          }
        />
        <Stat
          value={index(item.financial_profile_index)}
          label="Peer-relative financial profile"
          tone={item.financial_profile_index === null ? "muted" : "accent"}
          sub={
            item.peer_group
              ? item.peer_count
                ? `EMTAK ${item.peer_group} · ${item.peer_count} comparable peers`
                : `EMTAK ${item.peer_group} · no peer index`
              : "No peer group"
          }
        />
      </div>

      <div className="grid grid-2">
        <Panel title="What the filings show" description="Reported figures from the company's own annual-report filings.">
          {item.review_reasons.length > 0 ? (
            <ul className="dot-list">
              {item.review_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <EmptyState>No comparable annual figures.</EmptyState>
          )}
        </Panel>
        <Panel
          title="What the filings cannot show"
          description="The financial shortlist is a research cue, not evidence of owner intent."
        >
          <ul className="dot-list warn">
            {item.open_questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
          <dl className={styles.assessment}>
            <dt>Owner intent</dt>
            <dd>
              <Badge value={item.owner_intent} />
              <span className="small muted">Not assessed from filings; only an advisor conversation can establish it.</span>
            </dd>
            <dt>Buyer fit</dt>
            <dd>
              <Badge value={item.buyer_fit} label={item.buyer_fit.replace("_", " ")} tone="muted" />
              <span className="small muted">Needs MGX buyer criteria; not scored here.</span>
            </dd>
            <dt>Website signal</dt>
            <dd>
              {item.digital_decay_verdict ? (
                <>
                  <Badge value={item.digital_decay_verdict} />
                  <span className="small muted">checked {item.digital_decay_observed_at?.slice(0, 10) ?? "—"}</span>
                </>
              ) : (
                <span className="small muted">Not checked</span>
              )}
              <Link className="small" href={`/companies/${item.company_id}?tab=decay`}>
                Digital-decay checks →
              </Link>
            </dd>
          </dl>
        </Panel>
      </div>

      <CashHarvesting item={item} />

      <FilingsAndSources item={item} />

      <Panel title="How the funnel and this brief are built">
        <p className={styles.method}>{brief.methodology}</p>
      </Panel>
    </>
  );
}

/** Why this company does or does not sit in the current list, stated from the API's own classification. */
function FilterContext({ brief, filters }: { brief: SellerProspectBrief; filters: ProspectFilters }) {
  const { item } = brief;
  const clearSectorHref = `/seller-prospects/${item.company_id}?${filterQuery({ ...filters, sector: undefined })}`;
  const notices: ReactNode[] = [];

  if (filters.adjustments.length > 0) {
    notices.push(
      <div className="notice" role="status" key="adjusted">
        <strong>Some filter values in the address were adjusted:</strong>
        <ul>
          {filters.adjustments.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </div>,
    );
  }
  if (!brief.in_sector && filters.sector) {
    notices.push(
      <div className="notice notice-info" key="sector">
        <strong>Not in the selected sector {sectorName(filters.sector)} — shown for reference.</strong>{" "}
        Its division is {item.peer_group ? `EMTAK ${item.peer_group}` : "unknown"}, so it has no rank in this filter. <Link href={clearSectorHref}>Clear the sector filter</Link> to see its rank across all sectors.
      </div>,
    );
  }
  if (item.focus_band === "adjacent") {
    notices.push(
      <div className="notice" key="band">
        <strong>Outside the {bandLabel(filters)} size band.</strong> Latest comparable revenue{" "}
        {money(item.latest_revenue_eur)}
        {item.latest_year ? ` in FY${item.latest_year}` : ""}. The band classifies companies rather than removing them
        {brief.in_sector ? ", so it stays in the list" : ""}
        {brief.in_sector && item.next_action === "outside_size_band" ? ", marked “outside size band”." : "."}
      </div>,
    );
  } else if (item.focus_band !== "core") {
    notices.push(
      <div className="notice" key="band">
        <strong>No size band:</strong> there is no comparable standalone EUR annual report with revenue and operating profit, so the {bandLabel(filters)} band
        cannot classify this company.
      </div>,
    );
  }
  if (item.evidence_status !== "complete") {
    notices.push(
      <div className="notice" key="evidence">
        <strong>Financial evidence incomplete</strong> ({item.evidence_status.replace(/_/g, " ")}).
        {item.issues.length > 0 ? (
          <ul>
            {item.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        ) : (
          " No specific reason was recorded."
        )}
      </div>,
    );
  }
  return notices.length > 0 ? <div className={styles.context}>{notices}</div> : null;
}

function CashHarvesting({ item }: { item: SellerProspect }) {
  const insufficient = item.cash_harvesting_evidence_status === "insufficient_evidence";
  const cagr = item.cash_harvesting_revenue_cagr;
  const margin = item.latest_ebitda_margin;
  const cagrInBand = cagr !== null && cagr >= CAGR_MIN && cagr <= CAGR_MAX;
  const marginThreshold = item.cash_harvesting_margin_threshold;
  const marginBasis = item.cash_harvesting_margin_basis;
  const marginKnown = margin !== null && marginThreshold !== null;
  const marginAbove =
    marginKnown &&
    (marginBasis === "absolute" ? margin! > marginThreshold! : margin! > 0 && margin! >= marginThreshold!);
  const year = item.cash_harvesting_latest_year;
  const yearDiffers = year !== item.latest_year;

  const resultBadge = item.cash_harvesting_candidate ? (
    <Badge value="cash_harvesting_candidate" large />
  ) : insufficient ? (
    <Badge value="insufficient_evidence" large />
  ) : (
    <Badge value="not_a_candidate" label="Not a candidate" tone="muted" large />
  );

  return (
    <Panel
      title="Cash Harvesting candidate · financial description only"
      description="Both checks must be supported by populated and comparable values; no payout ratio, dividends or capex are used."
      actions={resultBadge}
      footnote="Label describes stable, high-margin, low-growth financials only. It does not claim cash extraction or sale intent."
    >
      {insufficient && (
        <div className={`notice ${styles.chNotice}`}>
          <strong>Insufficient evidence.</strong> Missing or incomparable inputs are never treated as zero.
          {item.cash_harvesting_issues.length > 0 ? (
            <ul>
              {item.cash_harvesting_issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          ) : (
            " No specific reason was recorded."
          )}
        </div>
      )}
      <div className={styles.gauges}>
        <div className={styles.gauge}>
          <div className={styles.gaugeLabel}>
            Revenue CAGR · {cagr !== null && year !== null ? `FY${year - 2}–FY${year}` : "3 years"}
          </div>
          {cagr === null ? (
            <UnknownGauge>
              Not available: it needs three consecutive comparable standalone EUR revenue years.
            </UnknownGauge>
          ) : (
            <>
              <div className={styles.gaugeHead}>
                <span className={`${styles.gaugeValue} ${cagrInBand ? styles.pass : styles.fail}`}>
                  {signedPercent(cagr)}
                </span>
                <span className={styles.gaugeText}>
                  {cagrInBand
                    ? "Inside the −2% to +3% candidate band"
                    : `Outside the −2% to +3% candidate band (${cagr < CAGR_MIN ? "below" : "above"})`}
                </span>
              </div>
              <div className={styles.track}>
                <Meter
                  value={(cagr + CAGR_SCALE) / (2 * CAGR_SCALE)}
                  tone={cagrInBand ? "good" : "warn"}
                  label={`Revenue CAGR ${signedPercent(cagr)} on a −10% to +10% scale`}
                />
                <span
                  className={styles.band}
                  style={{
                    left: `${((CAGR_MIN + CAGR_SCALE) / (2 * CAGR_SCALE)) * 100}%`,
                    width: `${((CAGR_MAX - CAGR_MIN) / (2 * CAGR_SCALE)) * 100}%`,
                  }}
                  aria-hidden="true"
                />
              </div>
              <div className={styles.scale} aria-hidden="true">
                <span style={{ left: 0 }}>−10%</span>
                <span className={styles.mark} style={{ left: "40%" }}>
                  −2%
                </span>
                <span className={styles.mark} style={{ left: "65%" }}>
                  +3%
                </span>
                <span style={{ left: "100%" }}>+10%</span>
              </div>
              <p className={styles.gaugeNote}>
                Dashed outline marks the −2% to +3% candidate band (inclusive).
                {Math.abs(cagr) > CAGR_SCALE && " The value lies beyond the displayed scale."}
              </p>
            </>
          )}
        </div>

        <div className={styles.gauge}>
          <div className={styles.gaugeLabel}>EBITDA margin · {year !== null ? `FY${year}` : "latest comparable year"}</div>
          {margin === null ? (
            <UnknownGauge>
              Not available: it needs a populated EBITDA for the latest comparable year. Operating margin is never
              substituted for it.
            </UnknownGauge>
          ) : !marginKnown ? (
            <UnknownGauge>{cashHarvestingBasisSentence(item)}</UnknownGauge>
          ) : (
            <>
              <div className={styles.gaugeHead}>
                <span className={`${styles.gaugeValue} ${marginAbove ? styles.pass : styles.fail}`}>{percent(margin)}</span>
                <span className={styles.gaugeText}>
                  {marginBasis === "absolute"
                    ? marginAbove
                      ? `Above the strict ${percent(marginThreshold)} absolute threshold`
                      : `At or below the strict ${percent(marginThreshold)} absolute threshold`
                    : marginAbove
                      ? `At or above the ${cashHarvestingBasisLabel(item)}`
                      : `Below the ${cashHarvestingBasisLabel(item)}`}
                </span>
              </div>
              <div className={styles.track}>
                <Meter
                  value={margin / EBITDA_SCALE}
                  tone={marginAbove ? "good" : "warn"}
                  label={`EBITDA margin ${percent(margin)} on a 0% to 40% scale`}
                />
                <span
                  className={styles.threshold}
                  style={{ left: `${Math.max(0, Math.min(100, (marginThreshold! / EBITDA_SCALE) * 100))}%` }}
                  aria-hidden="true"
                />
              </div>
              <div className={styles.scale} aria-hidden="true">
                <span style={{ left: 0 }}>0%</span>
                <span
                  className={styles.mark}
                  style={{ left: `${Math.max(0, Math.min(100, (marginThreshold! / EBITDA_SCALE) * 100))}%` }}
                >
                  {percent(marginThreshold)}
                </span>
                <span style={{ left: "100%" }}>40%</span>
              </div>
              <p className={styles.gaugeNote}>{cashHarvestingBasisSentence(item)}</p>
              {(margin < 0 || margin > EBITDA_SCALE || marginThreshold! > EBITDA_SCALE) && (
                <p className={styles.gaugeNote}>The value lies beyond the displayed scale.</p>
              )}
            </>
          )}
        </div>
      </div>
      <p className={styles.result}>
        <strong>Result: </strong>
        {item.cash_harvesting_candidate
          ? "Cash Harvesting candidate — both checks hold on populated, comparable values."
          : insufficient
            ? "Not triggered — the signal stays at insufficient evidence for the reasons listed above."
            : "Not a candidate — at least one of the two checks does not hold."}
      </p>
      <p className={styles.gaugeNote}>
        EBITDA: a reported EBITDA takes precedence. Otherwise the importer derives it as{" "}
        <code>operating_profit − depreciation_and_impairment</code>; the depreciation and impairment line keeps its
        reported negative sign, so subtracting it adds the expense back. Operating margin is never substituted.
      </p>

      <div className={`subpanel ${styles.chSources}`}>
        <h4 className={styles.subhead}>Sources for this signal</h4>
        <p className="small muted">
          Supports the Cash Harvesting revenue CAGR and EBITDA margin above; the general financial profile below selects
          its comparable years separately.
        </p>
        <dl className="kv">
          <dt>Latest comparable year</dt>
          <dd>
            {year !== null ? `FY${year}` : <span className="muted">unknown</span>}
            {yearDiffers && (
              <span className="cell-sub">
                Differs from the general financial profile ({item.latest_year !== null ? `FY${item.latest_year}` : "no comparable year"}
                ): this signal needs comparable revenue only; the general profile also needs operating profit.
              </span>
            )}
          </dd>
          <dt>Filings</dt>
          <dd>
            <FilingChips ids={item.cash_harvesting_filing_ids} empty="None recorded for this signal" />
          </dd>
          <dt>Source files</dt>
          <dd>
            <SourceFiles urls={item.cash_harvesting_source_urls} />
          </dd>
          {!insufficient && item.cash_harvesting_issues.length > 0 && (
            <>
              <dt>Recorded issues</dt>
              <dd>
                <ul className="dot-list warn" style={{ marginTop: 0 }}>
                  {item.cash_harvesting_issues.map((issue) => (
                    <li key={issue} style={{ fontSize: 14 }}>
                      {issue}
                    </li>
                  ))}
                </ul>
              </dd>
            </>
          )}
        </dl>
      </div>
    </Panel>
  );
}

function UnknownGauge({ children }: { children: ReactNode }) {
  return (
    <>
      <div className={styles.gaugeHead}>
        <span className={`${styles.gaugeValue} ${styles.unknown}`}>unknown</span>
      </div>
      <div className={styles.unknownBox}>{children}</div>
    </>
  );
}

function FilingsAndSources({ item }: { item: SellerProspect }) {
  const groupParent = item.flags.includes("group_parent");
  return (
    <Panel
      title="General financial profile · filings and sources"
      description={
        <>
          Supports the revenue, profit-year, margin, growth, equity, FTE and peer-index figures above
          {item.latest_year ? ` (latest comparable year FY${item.latest_year})` : ""}. The Cash Harvesting signal lists
          its own sources in its panel.
        </>
      }
    >
      <div className="grid grid-2" style={{ marginBottom: 0 }}>
        <div className="subpanel">
          <h4 className={styles.subhead}>Standalone EUR filings</h4>
          <dl className="kv">
            <dt>Evidence status</dt>
            <dd>
              <Badge value={item.evidence_status} />
            </dd>
            <dt>Filings</dt>
            <dd>
              <FilingChips ids={item.filing_ids} empty="No comparable report" />
            </dd>
            <dt>Source files</dt>
            <dd>
              <SourceFiles urls={item.source_urls} />
            </dd>
            <dt>Issues</dt>
            <dd>
              {item.issues.length > 0 ? (
                <ul className="dot-list warn" style={{ marginTop: 0 }}>
                  {item.issues.map((issue) => (
                    <li key={issue} style={{ fontSize: 14 }}>
                      {issue}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="muted">None recorded</span>
              )}
            </dd>
          </dl>
        </div>

        <div className="subpanel">
          <h4 className={styles.subhead}>Further reported values</h4>
          <dl className="kv">
            <dt>Latest operating margin</dt>
            <dd>
              {percent(item.latest_operating_margin)}
              {item.latest_year && item.latest_operating_margin !== null && (
                <span className="muted small"> · FY{item.latest_year}</span>
              )}
            </dd>
            <dt>Revenue growth</dt>
            <dd>
              {item.three_year_revenue_cagr === null ? "—" : `${signedPercent(item.three_year_revenue_cagr)} a year`}
              {item.three_year_revenue_cagr !== null && item.latest_year && (
                <span className="muted small">
                  {" "}
                  · FY{item.latest_year - 2}–FY{item.latest_year}
                </span>
              )}
            </dd>
            <dt>Equity / total assets</dt>
            <dd>
              {percent(item.latest_equity_ratio, 0)}
              {item.latest_year && item.latest_equity_ratio !== null && (
                <span className="muted small"> · FY{item.latest_year}</span>
              )}
            </dd>
            <dt>Reported FTE</dt>
            <dd>
              {item.latest_employees_fte === null ? "—" : `${item.latest_employees_fte.toFixed(0)} FTE`}
              {item.latest_year && item.latest_employees_fte !== null && (
                <span className="muted small"> · FY{item.latest_year}</span>
              )}
            </dd>
            {(groupParent || item.consolidated_revenue_eur !== null) && (
              <>
                <dt>Group / consolidated revenue</dt>
                <dd>
                  {item.consolidated_revenue_eur !== null ? (
                    <>
                      {money(item.consolidated_revenue_eur)}
                      {item.latest_year && (
                        <span className="muted small">
                          {" "}
                          · consolidated statement, FY{item.latest_year}; not one of the standalone filings listed
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="muted">Consolidated accounts filed; group revenue not uniquely reported in EUR</span>
                  )}
                </dd>
              </>
            )}
            <dt>Peer position</dt>
            <dd>
              {item.margin_peer_z !== null && item.equity_peer_z !== null ? (
                <>
                  Margin {index(item.margin_peer_z, 1)}, equity ratio {index(item.equity_peer_z, 1)}
                  <span className="cell-sub">
                    Robust SD from the EMTAK {item.peer_group} median (capped at ±3); index = 70% margin + 30% equity.
                  </span>
                </>
              ) : (
                <span className="muted">No peer index for this company</span>
              )}
            </dd>
          </dl>
        </div>
      </div>
    </Panel>
  );
}

function FilingChips({ ids, empty }: { ids: string[]; empty: string }) {
  if (ids.length === 0) return <span className="muted">{empty}</span>;
  return (
    <span className="chips">
      {ids.map((raw) => {
        const { year, id } = splitFilingId(raw);
        return (
          <span className="chip" key={raw} title="Fiscal year · annual-report filing ID">
            {year ? `FY${year} · ${id}` : id}
          </span>
        );
      })}
    </span>
  );
}

function SourceFiles({ urls }: { urls: string[] }) {
  if (urls.length === 0) return <span className="muted">None recorded</span>;
  return (
    <>
      <ul className={styles.sourceList}>
        {urls.map((url) => (
          <li key={url}>
            <a href={url} title="Official bulk dataset file (large ZIP)">
              {fileName(url)}
            </a>
          </li>
        ))}
      </ul>
      <span className="cell-sub">Official e-Business Register bulk dataset files (large ZIPs).</span>
    </>
  );
}
