import Link from "next/link";
import { Fragment } from "react";

import { Badge } from "@/components/Badge";
import { apiGet } from "@/lib/api";
import type { SellerFunnel } from "@/lib/types";

export const dynamic = "force-dynamic";

type SP = Record<string, string | undefined>;

function money(value: number | null): string {
  if (value === null) return "—";
  return `€${(value / 1_000_000).toFixed(1)}m`;
}

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function index(value: number | null): string {
  return value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

export default async function SellerProspectsPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const minMillions = Number(sp.min_millions ?? 5);
  const maxMillions = Number(sp.max_millions ?? 50);
  const min = Number.isFinite(minMillions) && minMillions > 0 ? minMillions : 5;
  const max = Number.isFinite(maxMillions) && maxMillions >= min ? maxMillions : Math.max(min, 50);
  const view = sp.view === "all" ? "all" : "cash_harvesting";
  const hideActive = sp.hide_active_decay === "true";
  const data = await apiGet<SellerFunnel>("/seller-prospects", {
    min_revenue_eur: Math.round(min * 1_000_000),
    max_revenue_eur: Math.round(max * 1_000_000),
    sector: sp.sector,
    view,
    hide_active_decay: hideActive ? "true" : undefined,
    limit: 100,
  });
  const top = data.stages[0]?.count || 1;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Seller prospect funnel</h1>
          <p className="subtitle">
            Which companies deserve an advisor&apos;s research first, and why. A financial profile is not evidence that an owner wants to sell.
          </p>
          {view === "cash_harvesting" && (
            <p className="subtitle">
              Showing Cash Harvesting candidates (stable revenue, EBITDA margin above 15%), ordered by website timing signal:
              coasting, decaying and watch first, unchecked next, active last. Unchecked companies are never hidden.
            </p>
          )}
        </div>
      </div>

      <form className="panel filters" method="get">
        <label>
          Show
          <select name="view" defaultValue={view}>
            <option value="cash_harvesting">Cash Harvesting candidates</option>
            <option value="all">All companies</option>
          </select>
        </label>
        <label>
          Minimum revenue (€m)
          <input name="min_millions" type="number" min="0.1" step="0.1" defaultValue={sp.min_millions ?? "5"} />
        </label>
        <label>
          Maximum revenue (€m)
          <input name="max_millions" type="number" min="0.1" step="0.1" defaultValue={sp.max_millions ?? "50"} />
        </label>
        <label>
          Sector
          <select name="sector" defaultValue={sp.sector ?? ""}>
            <option value="">All sectors</option>
            {data.sector_options.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.count.toLocaleString("en")})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>
            <input type="checkbox" name="hide_active_decay" value="true" defaultChecked={hideActive} /> Hide companies whose website check is active
          </span>
        </label>
        <button className="btn btn-primary">Apply</button>
        <Link className="btn btn-ghost" href="/seller-prospects">Reset</Link>
      </form>

      <section className="panel">
        <h2>From registry to advisor queue</h2>
        <p className="muted">Each step keeps only the companies that pass its rule. Every count comes from the official annual-report files.</p>
        {data.stages.length === 0 ? (
          <p className="muted">No imported Estonian companies yet. Load the official register files first.</p>
        ) : (
          <div className="funnel">
            {data.stages.map((stage, i) => {
              const previous = i > 0 ? data.stages[i - 1].count : null;
              const final = i === data.stages.length - 1;
              return (
                <div className="funnel-row" key={stage.key}>
                  <div>
                    <strong>{stage.label}</strong>
                    <div className="small muted">{stage.rule}</div>
                  </div>
                  <div className="funnel-bar">
                    <div
                      className={final ? "funnel-fill final" : "funnel-fill"}
                      style={{ width: `${Math.max(1, (stage.count / top) * 100)}%` }}
                    />
                  </div>
                  <div className="num">
                    <span className="count">{stage.count.toLocaleString("en")}</span>
                    {previous !== null && previous > stage.count && (
                      <div className="small muted">−{(previous - stage.count).toLocaleString("en")}</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel">
        <h2>Prioritised companies</h2>
        <p className="muted">
          Showing {Math.min(data.items.length, 100)} of {data.listed_companies.toLocaleString("en")}{" "}
          {view === "cash_harvesting" ? "Cash Harvesting candidates" : "companies"}.{" "}
          Website checks: {data.listed_decay_checked} checked, {data.listed_decay_flagged} flagged coasting, decaying or watch.{" "}
          The peer index ranks a financial profile inside its EMTAK group; it does not score sale readiness.
          Open a row to see what the filings show and what they cannot.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Next action</th>
                <th className="num">Revenue</th>
                <th className="num">3-year profit</th>
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
                  <tr>
                    <td>
                      <Link href={`/companies/${item.company_id}`}><strong>{item.legal_name}</strong></Link>
                      <div className="small muted">
                        {item.registry_id?.replace("EE:", "") ?? "No registry ID"}
                        {item.registry_url && (
                          <> · <a href={item.registry_url} target="_blank" rel="noreferrer">Register card ↗</a></>
                        )}
                      </div>
                      {item.flags.length > 0 && (
                        <div>{item.flags.map((flag) => <Badge key={flag} value={flag} />)}</div>
                      )}
                    </td>
                    <td><Badge value={item.next_action} /></td>
                    <td className="num">
                      {money(item.latest_revenue_eur)}
                      <div className="small muted">
                        {item.latest_year ? `FY${item.latest_year}` : "—"}
                        {item.latest_employees_fte !== null && ` · ${item.latest_employees_fte.toFixed(0)} FTE`}
                      </div>
                    </td>
                    <td className="num">{item.positive_profit_years === null ? "—" : `${item.positive_profit_years}/3`}</td>
                    <td className="num">{percent(item.three_year_median_margin)}</td>
                    <td className="num">
                      {percent(item.latest_ebitda_margin)}
                      <div className="small muted">
                        EBITDA margin · CAGR {item.cash_harvesting_revenue_cagr === null ? "—" : `${item.cash_harvesting_revenue_cagr >= 0 ? "+" : ""}${(item.cash_harvesting_revenue_cagr * 100).toFixed(1)}%`}
                      </div>
                    </td>
                    <td className="num" title={item.peer_count ? `${item.peer_count} peers in ${item.peer_group_label ?? item.peer_group}` : "Insufficient comparable peers"}>
                      {index(item.financial_profile_index)}
                      <div className="small muted">
                        {item.peer_group_label ?? (item.peer_group ? `EMTAK ${item.peer_group}` : "No peer group")}
                        {item.peer_count ? ` · ${item.peer_count} peers` : ""}
                      </div>
                    </td>
                    <td className="small">
                      {item.digital_decay_verdict ? (
                        <>
                          <Badge value={item.digital_decay_verdict} />
                          <div className="small muted">checked {item.digital_decay_observed_at?.slice(0, 10)}</div>
                        </>
                      ) : (
                        <span className="muted">not checked</span>
                      )}
                    </td>
                    <td className="small">
                      <Badge value={item.evidence_status} />
                      <div>{item.filing_ids.join(", ") || "No comparable report"}</div>
                      {item.source_urls[0] && (
                        <a href={item.source_urls[0]} title="Official bulk dataset file (large ZIP)">Dataset file</a>
                      )}
                      {item.issues.length > 0 && <div className="muted" title={item.issues.join("; ")}>{item.issues[0]}</div>}
                    </td>
                  </tr>
                  <tr className="brief-row">
                    <td colSpan={9}>
                      <details>
                        <summary>Why review · what we don&apos;t know</summary>
                        <div className="brief">
                          <div>
                            <strong>What the filings show</strong>
                            {item.review_reasons.length > 0 ? (
                              <ul>{item.review_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                            ) : (
                              <p className="muted">No comparable annual figures.</p>
                            )}
                          </div>
                          <div>
                            <strong>What they cannot show</strong>
                            <ul>{item.open_questions.map((question) => <li key={question}>{question}</li>)}</ul>
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
        {data.items.length === 0 && (
          <p className="muted">
            {view === "cash_harvesting"
              ? "No Cash Harvesting candidates match these filters."
              : "No companies match, or the official Estonia files have not been loaded yet."}
          </p>
        )}
      </section>

      {data.peer_groups.length > 0 && (
        <section className="panel">
          <h2>Where a peer index exists</h2>
          <p className="muted">Sectors (EMTAK divisions) with at least eight comparable, active companies in the size band. Smaller groups get no index.</p>
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
                      <Link
                        href={`/seller-prospects?sector=${group.group}&min_millions=${min}&max_millions=${max}&view=${view}${hideActive ? "&hide_active_decay=true" : ""}`}
                      >
                        {group.label || group.group}
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
        </section>
      )}

      <section className="panel">
        <h2>How this funnel works</h2>
        <p>{data.methodology}</p>
        <p><strong>What an advisor does with a shortlisted company</strong></p>
        <ol>
          <li>Check who owns it on the register card: founder, family, group or fund.</li>
          <li>Check whether Mergero&apos;s buyers want this profile in MGX.</li>
          <li>If both hold, open with something useful to the owner: valuation insight or a discreet test of buyer appetite.</li>
          <li>Record the outcome, so the funnel learns which profiles turn into conversations and mandates.</li>
        </ol>
      </section>
    </>
  );
}
