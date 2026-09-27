import Link from "next/link";
import { Fragment } from "react";

import { Badge } from "@/components/Badge";
import { EmptyState, Meter, PageHeader, Panel } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { CompanyPage, SellerFunnel } from "@/lib/types";

import {
  PAGE_SIZE,
  bandLabel,
  filterParams,
  filterQuery,
  index,
  money,
  parseFilters,
  parseOffset,
  percent,
  sectorName,
  signedPercent,
  type SP,
} from "./prospect-format";
import styles from "./prospects.module.css";

export const dynamic = "force-dynamic";

const SEARCH_RESULT_LIMIT = 8;
const EXAMPLE_COUNT = 4;

export default async function SellerProspectsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const filters = parseFilters(sp);
  const offset = parseOffset(sp);
  const query = sp.q?.trim() ?? "";
  const [data, companyMatches] = await Promise.all([
    apiGet<SellerFunnel>("/seller-prospects", {
      ...filterParams(filters),
      limit: PAGE_SIZE,
      offset,
    }),
    query
      ? apiGet<CompanyPage>("/companies", { q: query, min_employees: 0, page_size: SEARCH_RESULT_LIMIT })
      : Promise.resolve(null),
  ]);
  const top = data.stages[0]?.count || 1;
  const knownSector = !filters.sector || data.sector_options.some((opt) => opt.code === filters.sector);
  const total = data.total_items;
  const first = offset + 1;
  const last = offset + data.items.length;
  // Paging lands on the list itself rather than the top of the page.
  const pageHref = (to: number) => `/seller-prospects?${filterQuery(filters, to)}#prospects`;
  // A search keeps the current filters but always restarts at the first page of matches.
  const searchHref = (name: string) => `/seller-prospects?${filterQuery(filters)}&q=${encodeURIComponent(name)}#lookup`;
  const clearSearchHref = `/seller-prospects?${filterQuery(filters, offset)}`;
  const showing =
    data.items.length > 0
      ? `Showing ${first.toLocaleString("en")}–${last.toLocaleString("en")} of ${total.toLocaleString("en")}`
      : `${total.toLocaleString("en")} ${total === 1 ? "company" : "companies"}`;
  const listNoun = filters.view === "cash_harvesting" ? "Cash Harvesting candidates" : "companies";

  // Example searches are real companies already in the current result page, never invented names.
  const examples = Array.from(new Set(data.items.map((item) => item.legal_name))).slice(0, EXAMPLE_COUNT);

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Estonia database" }, { label: "Seller prospects" }]}
        title="Seller prospect funnel"
        subtitle={
          <>
            Which companies merit an advisor&apos;s research first, and why. A financial profile is not evidence that an
            owner wants to sell.
          </>
        }
      />

      <Panel id="lookup" className={styles.searchPanel}>
        <form className={styles.searchForm} method="get" role="search" aria-label="Look up a company">
          <label htmlFor="prospect-search" className={styles.searchLabel}>
            Look up a company
          </label>
          <div className={styles.searchRow}>
            <input
              id="prospect-search"
              name="q"
              type="search"
              defaultValue={query}
              placeholder="Company name or trading name"
              autoComplete="off"
            />
            <button className="btn btn-primary" type="submit">
              Search
            </button>
          </div>
          {/* Carries the current list filters through a search, so results and the funnel below stay in sync. */}
          <input type="hidden" name="min_millions" value={String(filters.min)} />
          <input type="hidden" name="max_millions" value={String(filters.max)} />
          {filters.sector && <input type="hidden" name="sector" value={filters.sector} />}
          {filters.view !== "cash_harvesting" && <input type="hidden" name="view" value={filters.view} />}
          {filters.hideActiveDecay && <input type="hidden" name="hide_active_decay" value="true" />}
        </form>

        {!query && examples.length > 0 && (
          <p className={styles.searchExamples}>
            Try:{" "}
            {examples.map((name, i) => (
              <Fragment key={name}>
                {i > 0 && " · "}
                <Link href={searchHref(name)}>{name}</Link>
              </Fragment>
            ))}
          </p>
        )}

        {query && (
          <div className={styles.searchResults}>
            {companyMatches && companyMatches.items.length > 0 ? (
              <>
                <p className="small muted" style={{ margin: "0 0 8px" }}>
                  {companyMatches.total.toLocaleString("en")}{" "}
                  {companyMatches.total === 1 ? "company matches" : "companies match"} “{query}”
                  {companyMatches.total > companyMatches.items.length ? ` (showing first ${companyMatches.items.length})` : ""}.{" "}
                  <Link href={clearSearchHref}>Clear search</Link>
                </p>
                <ul className={styles.searchList}>
                  {companyMatches.items.map((c) => (
                    <li key={c.id}>
                      <Link href={`/companies/${c.id}`}>
                        <strong>{c.legal_name}</strong>
                      </Link>
                      <span className="cell-sub">
                        {c.registry_id?.replace("EE:", "") ?? "No registry ID"}
                        {" · "}
                        {c.sector_label ?? (c.sector ?? "sector unknown")}
                        {c.city ? ` · ${c.city}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <EmptyState>
                No company matches “{query}”. Try a shorter name, or check the spelling. <Link href={clearSearchHref}>Clear search</Link>.
              </EmptyState>
            )}
          </div>
        )}
      </Panel>

      {/* Keyed by the normalised filters, so after every navigation the inputs show exactly what was applied. */}
      <form
        key={filterQuery(filters)}
        className={`panel filters ${styles.filterForm}`}
        method="get"
        aria-label="Filter prospects"
      >
        <h2 className="filters-title">Filter prospects</h2>
        <label>
          Show
          <select name="view" defaultValue={filters.view}>
            <option value="cash_harvesting">Cash Harvesting candidates</option>
            <option value="all">All companies</option>
          </select>
        </label>
        <label>
          Min revenue (€m)
          <input name="min_millions" type="number" min="0.1" step="0.1" defaultValue={String(filters.min)} />
        </label>
        <label>
          Max revenue (€m)
          <input name="max_millions" type="number" min="0.1" step="0.1" defaultValue={String(filters.max)} />
        </label>
        <label className={styles.sectorField}>
          Sector · EMTAK
          <select name="sector" defaultValue={filters.sector ?? ""}>
            <option value="">All sectors</option>
            {!knownSector && filters.sector && (
              <option value={filters.sector}>{sectorName(filters.sector)} (not a listed division)</option>
            )}
            {data.sector_options.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.count.toLocaleString("en")})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>
            <input type="checkbox" name="hide_active_decay" value="true" defaultChecked={filters.hideActiveDecay} /> Hide
            companies whose website check is active
          </span>
        </label>
        <div className="filters-actions">
          <button className="btn btn-primary" type="submit">
            Apply filters
          </button>
          <Link className="btn btn-ghost" href="/seller-prospects">
            Reset
          </Link>
        </div>
        <p className={`form-hint ${styles.filterNote}`}>
          <strong>Show</strong> selects which list is returned: Cash Harvesting candidates (the default; a stable,
          high-margin, low-growth financial signal, not evidence of seller intent, based on financial criteria
          only), or every company instead. The revenue band classifies companies; it does not remove
          them. A company whose latest comparable standalone EUR revenue lies inside {bandLabel(filters)} is{" "}
          <strong>in the size band</strong> (core); one outside it stays in the list as{" "}
          <strong>outside size band</strong> (adjacent); one without a comparable annual report (revenue and operating
          profit) has no band (unknown). Digital Decay is opt-in and read-only here: hiding an &quot;active&quot; result
          only applies when the checkbox above is selected; an unchecked company is always kept and shown as
          &quot;Not checked&quot;, never treated as a negative signal.
        </p>
        {(filters.adjustments.length > 0 || !knownSector) && (
          <div className={`notice ${styles.adjusted}`} role="status">
            <strong>Some values in the address were adjusted:</strong>
            <ul>
              {filters.adjustments.map((note) => (
                <li key={note}>{note}</li>
              ))}
              {!knownSector && filters.sector && (
                <li>
                  Sector “{filters.sector}” is not one of the listed EMTAK divisions, so no company can match it.
                </li>
              )}
            </ul>
          </div>
        )}
      </form>

      <Panel
        title="From registry to advisor queue"
        description="Each step keeps only the companies that pass its rule. Every count comes from the official annual-report files."
      >
        {data.stages.length === 0 ? (
          <EmptyState>No imported Estonian companies yet. Load the official register files first.</EmptyState>
        ) : (
          <div className={styles.stages}>
            {data.stages.map((stage, i) => {
              const previous = i > 0 ? data.stages[i - 1].count : null;
              const final = i === data.stages.length - 1;
              return (
                <div className={styles.stage} key={stage.key}>
                  <div className={styles.stageLabel}>{stage.label}</div>
                  <div className={styles.stageRule}>{stage.rule}</div>
                  <div className={styles.stageBar}>
                    <Meter
                      value={stage.count / top}
                      tone={final ? "final" : undefined}
                      label={`${stage.label}: ${stage.count.toLocaleString("en")} of ${top.toLocaleString("en")}`}
                    />
                  </div>
                  <div className={styles.stageCount}>
                    <span className={styles.stageNumber}>{stage.count.toLocaleString("en")}</span>
                    {previous !== null && (
                      <div className={styles.stageDrop}>−{(previous - stage.count).toLocaleString("en")} removed</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel
        id="prospects"
        className={styles.listPanel}
        title="Prioritised companies"
        actions={<span className={styles.showing}>{showing}</span>}
        description={
          <>
            Advisor-review queue first, then research, outside size band and excluded. The peer index ranks financial
            profiles within comparable EMTAK groups; it is not a sale-readiness score. Cash Harvesting is shown as an
            informational signal on every row regardless of the selected view. Open a row to see what the filings show
            and what they cannot, or open the full evidence brief.
          </>
        }
      >
        {data.items.length === 0 ? (
          total > 0 && offset >= total ? (
            <EmptyState>
              This page starts after the last of {total.toLocaleString("en")} {listNoun}.{" "}
              <Link href={pageHref(0)}>Go to the first page</Link>.
            </EmptyState>
          ) : (
            <EmptyState>
              {filters.view === "cash_harvesting"
                ? "No Cash Harvesting candidates match these filters. Switch “Show” to all companies to see the full funnel."
                : "No companies match, or the official Estonia files have not been loaded yet."}
            </EmptyState>
          )
        ) : (
          <div className="table-wrap">
            <table className={styles.prospectTable}>
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Next action</th>
                  <th className="num">Revenue</th>
                  <th className="num">3yr profit</th>
                  <th className="num">Median margin</th>
                  <th className="num">Cash Harvesting</th>
                  <th className="num">Peer index</th>
                  <th>Website signal</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <Fragment key={item.company_id}>
                    <tr className={styles.mainRow}>
                      <td className={styles.companyCell}>
                        <Link href={`/companies/${item.company_id}`}>
                          <strong className={styles.companyName}>{item.legal_name}</strong>
                        </Link>
                        <span className="cell-sub">
                          {item.registry_id?.replace("EE:", "") ?? "No registry ID"}
                          {item.registry_url && (
                            <>
                              {" · "}
                              <a href={item.registry_url} target="_blank" rel="noreferrer">
                                Register card ↗
                              </a>
                            </>
                          )}
                        </span>
                        {item.flags.length > 0 && (
                          <div className={styles.flags}>
                            {item.flags.map((flag) => (
                              <Badge key={flag} value={flag} />
                            ))}
                          </div>
                        )}
                        <Link className={styles.briefLink} href={`/seller-prospects/${item.company_id}?${filterQuery(filters, offset)}`}>
                          Evidence brief →
                        </Link>
                      </td>
                      <td>
                        <Badge value={item.next_action} />
                      </td>
                      <td className="num">
                        <strong>{money(item.latest_revenue_eur)}</strong>
                        <span className="cell-sub">
                          {item.latest_year ? `FY${item.latest_year}` : "—"}
                          {item.latest_employees_fte !== null && ` · ${item.latest_employees_fte.toFixed(0)} FTE`}
                        </span>
                      </td>
                      <td className="num">
                        {item.positive_profit_years === null ? "—" : `${item.positive_profit_years} / 3`}
                      </td>
                      <td className="num">{percent(item.three_year_median_margin)}</td>
                      <td className="num" title="Informational only: stable revenue and a high EBITDA margin, not a filter unless Show is set to Cash Harvesting candidates.">
                        <strong>{percent(item.latest_ebitda_margin)}</strong>
                        <span className="cell-sub">EBITDA margin · CAGR {signedPercent(item.cash_harvesting_revenue_cagr)}</span>
                      </td>
                      <td
                        className="num"
                        title={
                          item.peer_count
                            ? `${item.peer_count} peers in ${item.peer_group_label ?? `EMTAK ${item.peer_group}`}`
                            : "Insufficient comparable peers"
                        }
                      >
                        <strong>{index(item.financial_profile_index)}</strong>
                        <span className="cell-sub">
                          {item.peer_group_label ?? (item.peer_group ? `EMTAK ${item.peer_group}` : "No peer group")}
                          {item.peer_count ? ` · ${item.peer_count} peers` : ""}
                        </span>
                      </td>
                      <td>
                        {item.digital_decay_verdict ? (
                          <>
                            <Badge value={item.digital_decay_verdict} />
                            <span className="cell-sub">checked {item.digital_decay_observed_at?.slice(0, 10) ?? "—"}</span>
                          </>
                        ) : (
                          <span className="muted">Not checked</span>
                        )}
                      </td>
                      <td className={styles.evidenceCell}>
                        <Badge value={item.evidence_status} />
                        <div className={styles.filingIds}>{item.filing_ids.join(", ") || "No comparable report"}</div>
                        {item.source_urls[0] && (
                          <a className="small" href={item.source_urls[0]} title="Official bulk dataset file (large ZIP)">
                            Dataset file
                          </a>
                        )}
                        {item.issues.length > 0 && (
                          <div className={styles.issue} title={item.issues.join("; ")}>
                            {item.issues[0]}
                          </div>
                        )}
                      </td>
                    </tr>
                    <tr className={styles.briefRow}>
                      <td colSpan={9}>
                        <details>
                          <summary>Why review · what we don&apos;t know</summary>
                          <div className={styles.inlineBrief}>
                            <div>
                              <h4>What the filings show</h4>
                              {item.review_reasons.length > 0 ? (
                                <ul className="dot-list">
                                  {item.review_reasons.map((reason) => (
                                    <li key={reason}>{reason}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="muted small" style={{ margin: "8px 0 0" }}>
                                  No comparable annual figures.
                                </p>
                              )}
                            </div>
                            <div>
                              <h4>What they cannot show</h4>
                              <ul className="dot-list warn">
                                {item.open_questions.map((question) => (
                                  <li key={question}>{question}</li>
                                ))}
                              </ul>
                            </div>
                          </div>
                        </details>
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > 0 && (
          <nav className="pagination" aria-label="Prospect pages">
            {offset > 0 ? (
              <Link href={pageHref(Math.max(0, offset - PAGE_SIZE))} className={`btn ${styles.pageBtn}`} rel="prev">
                ‹ Previous
              </Link>
            ) : (
              <span className={`btn ${styles.pageBtn}`} aria-disabled="true">
                ‹ Previous
              </span>
            )}
            <span className="muted">{showing}</span>
            {offset + PAGE_SIZE < total ? (
              <Link href={pageHref(offset + PAGE_SIZE)} className={`btn ${styles.pageBtn}`} rel="next">
                Next ›
              </Link>
            ) : (
              <span className={`btn ${styles.pageBtn}`} aria-disabled="true">
                Next ›
              </span>
            )}
          </nav>
        )}
      </Panel>

      {data.peer_groups.length > 0 && (
        <Panel
          title="Where a peer index exists"
          description="EMTAK groups with at least eight comparable, active companies in the size band. Smaller groups get no index."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sector</th>
                  <th className="num">Peers</th>
                  <th className="num">Median 3-year margin</th>
                  <th className="num">Median equity / assets</th>
                </tr>
              </thead>
              <tbody>
                {data.peer_groups.map((group) => (
                  <tr key={group.group}>
                    <td>
                      <Link href={`/seller-prospects?${filterQuery({ ...filters, sector: group.group })}`}>
                        <strong>{group.label || `EMTAK ${group.group}`}</strong>
                      </Link>
                    </td>
                    <td className="num">{group.peer_count}</td>
                    <td className="num">{percent(group.median_margin)}</td>
                    <td className="num">{percent(group.median_equity_ratio)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      <Panel title="How this funnel works">
        <p className={styles.method}>{data.methodology}</p>
        <h3>What an advisor does with a shortlisted company</h3>
        <ol className={styles.steps}>
          <li>Check who owns it on the register card: founder, family, group or fund.</li>
          <li>Check whether Mergero&apos;s buyers want this profile in MGX.</li>
          <li>If both hold, open with something useful to the owner: valuation insight or a discreet test of buyer appetite.</li>
          <li>Record the outcome, so the funnel learns which profiles turn into conversations and mandates.</li>
        </ol>
      </Panel>
    </>
  );
}
