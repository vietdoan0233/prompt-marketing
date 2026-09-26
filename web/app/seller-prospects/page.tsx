import Link from "next/link";

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
  const data = await apiGet<SellerFunnel>("/seller-prospects", {
    min_revenue_eur: Math.round(min * 1_000_000),
    max_revenue_eur: Math.round(max * 1_000_000),
    sector: sp.sector,
    limit: 100,
  });

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Seller prospect funnel</h1>
          <p className="subtitle">
            Screen imported companies for an advisor to investigate. A financial profile is not evidence that an owner wants to sell.
          </p>
        </div>
      </div>

      <form className="panel filters" method="get">
        <label>
          Minimum revenue (€m)
          <input name="min_millions" type="number" min="0.1" step="0.1" defaultValue={sp.min_millions ?? "5"} />
        </label>
        <label>
          Maximum revenue (€m)
          <input name="max_millions" type="number" min="0.1" step="0.1" defaultValue={sp.max_millions ?? "50"} />
        </label>
        <label>
          Sector or EMTAK group
          <input name="sector" defaultValue={sp.sector ?? ""} placeholder="e.g. 62" />
        </label>
        <button className="btn btn-primary">Apply</button>
        <Link className="btn btn-ghost" href="/seller-prospects">Reset</Link>
      </form>

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        <div className="stat"><div className="n">{data.total_companies}</div><div className="l">Imported companies</div></div>
        <div className="stat"><div className="n">{data.core_size}</div><div className="l">In size band</div></div>
        <div className="stat"><div className="n">{data.three_year_profitable}</div><div className="l">In band, 3 profitable years</div></div>
        <div className="stat"><div className="n">{data.advisor_review}</div><div className="l">Ready for advisor review</div></div>
      </div>

      <section className="panel">
        <h2>Prioritised companies</h2>
        <p className="muted">Showing up to 100 companies. The peer index ranks a financial profile among comparable EMTAK groups; it does not score sale readiness.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Company</th>
                <th>Next action</th>
                <th className="num">Revenue</th>
                <th className="num">3-year profit</th>
                <th className="num">Median margin</th>
                <th className="num">Peer index</th>
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.company_id}>
                  <td>
                    <Link href={`/companies/${item.company_id}`}><strong>{item.legal_name}</strong></Link>
                    <div className="small muted">{item.registry_id ?? "No registry ID"} · {item.sector ?? "Sector unknown"}</div>
                  </td>
                  <td><Badge value={item.next_action} /></td>
                  <td className="num">{money(item.latest_revenue_eur)}<div className="small muted">{item.latest_year ?? "—"}</div></td>
                  <td className="num">{item.positive_profit_years === null ? "—" : `${item.positive_profit_years}/3`}</td>
                  <td className="num">{percent(item.three_year_median_margin)}</td>
                  <td className="num" title={item.peer_count ? `${item.peer_count} peers in EMTAK ${item.peer_group}` : "Insufficient comparable peers"}>
                    {index(item.financial_profile_index)}
                    <div className="small muted">{item.peer_group ? `EMTAK ${item.peer_group}` : "No peer group"}</div>
                  </td>
                  <td className="small">
                    <Badge value={item.evidence_status} />
                    <div>{item.filing_ids.join(", ") || "No comparable report"}</div>
                    {item.source_urls[0] && <a href={item.source_urls[0]} target="_blank" rel="noreferrer">Official source ↗</a>}
                    {item.issues.length > 0 && <div className="muted" title={item.issues.join("; ")}>{item.issues[0]}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items.length === 0 && <p className="muted">No companies match, or the official Estonia files have not been loaded yet.</p>}
      </section>

      <section className="panel">
        <h2>How this funnel works</h2>
        <p>{data.methodology}</p>
        <p className="muted">Next step for each shortlisted company: an advisor checks strategic buyer fit and a credible reason to approach. Both are currently unknown in this dataset.</p>
      </section>
    </>
  );
}
