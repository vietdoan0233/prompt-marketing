import Link from "next/link";

import { Badge } from "@/components/Badge";
import { apiGet, fmtDate, fmtEmployees } from "@/lib/api";
import type { CompanyPage } from "@/lib/types";

export const dynamic = "force-dynamic";

type SP = Record<string, string | undefined>;

const COUNTRIES = ["EE"];

function sortHref(sp: SP, key: string): string {
  const next = new URLSearchParams(Object.entries(sp).filter(([, v]) => v) as [string, string][]);
  const same = (sp.sort ?? "legal_name") === key;
  next.set("sort", key);
  next.set("order", same && (sp.order ?? "asc") === "asc" ? "desc" : "asc");
  next.delete("page");
  return `/companies?${next.toString()}`;
}

function pageHref(sp: SP, page: number): string {
  const next = new URLSearchParams(Object.entries(sp).filter(([, v]) => v) as [string, string][]);
  next.set("page", String(page));
  return `/companies?${next.toString()}`;
}

export default async function CompaniesPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const minEmployees = sp.min_employees ?? "20";
  const data = await apiGet<CompanyPage>("/companies", {
    country: sp.country,
    region: sp.region,
    sector: sp.sector,
    min_employees: minEmployees,
    max_employees: sp.max_employees,
    review_status: sp.review_status,
    qualification: sp.qualification,
    freshness: sp.freshness,
    q: sp.q,
    sort: sp.sort,
    order: sp.order,
    page: sp.page,
    page_size: 50,
  });
  const arrow = (key: string) =>
    (sp.sort ?? "legal_name") === key ? ((sp.order ?? "asc") === "asc" ? " ▲" : " ▼") : "";
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Company database</h1>
          <p className="subtitle">
            {data.total} companies match.{" "}
            {minEmployees === "20" && "Default view: ≥20 employees (primary viability proxy). "}
            Every value is traceable to source facts; uncertainty is shown, never hidden.
          </p>
        </div>
      </div>

      <form className="panel filters" method="get">
        <label>
          Search
          <input name="q" defaultValue={sp.q} placeholder="name" />
        </label>
        <label>
          Country
          <select name="country" defaultValue={sp.country ?? ""}>
            <option value="">All</option>
            {COUNTRIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Sector / NACE
          <input name="sector" defaultValue={sp.sector} placeholder="e.g. software, 62" />
        </label>
        <label>
          Min employees
          <select name="min_employees" defaultValue={minEmployees}>
            <option value="0">All (incl. sub-scale & unknown)</option>
            <option value="20">≥ 20 (default)</option>
            <option value="50">≥ 50</option>
            <option value="100">≥ 100</option>
            <option value="250">≥ 250</option>
          </select>
        </label>
        <label>
          Max employees
          <input name="max_employees" type="number" min={0} defaultValue={sp.max_employees} style={{ width: 90 }} />
        </label>
        <label>
          Qualification
          <select name="qualification" defaultValue={sp.qualification ?? ""}>
            <option value="">Any</option>
            <option value="qualified">≥20 qualified</option>
            <option value="borderline">borderline</option>
            <option value="below_threshold">below 20</option>
            <option value="sub_scale">sub-scale (1–2)</option>
            <option value="unknown_headcount">headcount unknown</option>
          </select>
        </label>
        <label>
          Review
          <select name="review_status" defaultValue={sp.review_status ?? ""}>
            <option value="">Any</option>
            <option value="unreviewed">unreviewed</option>
            <option value="reviewed">reviewed</option>
            <option value="needs_correction">needs correction</option>
          </select>
        </label>
        <label>
          Freshness
          <select name="freshness" defaultValue={sp.freshness ?? ""}>
            <option value="">Any</option>
            <option value="fresh">fresh</option>
            <option value="aging">aging</option>
            <option value="stale">stale</option>
          </select>
        </label>
        <input type="hidden" name="sort" value={sp.sort ?? ""} />
        <input type="hidden" name="order" value={sp.order ?? ""} />
        <button className="btn btn-primary">Apply</button>
        <Link href="/companies" className="btn btn-ghost">
          Reset
        </Link>
      </form>

      <section className="panel">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <Link href={sortHref(sp, "legal_name")}>Company{arrow("legal_name")}</Link>
                </th>
                <th>
                  <Link href={sortHref(sp, "country")}>Country{arrow("country")}</Link>
                </th>
                <th>
                  <Link href={sortHref(sp, "sector")}>Sector{arrow("sector")}</Link>
                </th>
                <th className="num">
                  <Link href={sortHref(sp, "employees")}>Employees{arrow("employees")}</Link>
                </th>
                <th>
                  <Link href={sortHref(sp, "qualification")}>Qualification{arrow("qualification")}</Link>
                </th>
                <th>Provenance</th>
                <th>
                  <Link href={sortHref(sp, "last_verified_at")}>Freshness{arrow("last_verified_at")}</Link>
                </th>
                <th>
                  <Link href={sortHref(sp, "completeness")}>Complete{arrow("completeness")}</Link>
                </th>
                <th>Review</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.id} className={c.qualification_status === "sub_scale" ? "row-muted" : ""}>
                  <td>
                    <Link href={`/companies/${c.id}`}>
                      <strong>{c.legal_name}</strong>
                    </Link>
                    <div className="small muted">
                      {c.registry_id ? <span className="mono">{c.registry_id}</span> : <em>no registry ID</em>}
                      {c.city ? ` · ${c.city}` : ""}
                    </div>
                  </td>
                  <td>{c.country}</td>
                  <td className="small">
                    {c.sector ?? <span className="muted">—</span>}
                    {c.industry_code_details.length > 0 ? (
                      c.industry_code_details.map((item) => (
                        <div className="muted mono" key={`${item.code_system}:${item.code_version}:${item.code}`}>
                          {item.code_system ?? "Code system unknown"} {item.code_version ?? "version unknown"}: {item.code}
                        </div>
                      ))
                    ) : (
                      c.industry_codes[0] && <div className="muted mono">{c.industry_codes[0]} · version unknown</div>
                    )}
                  </td>
                  <td className="num">
                    {fmtEmployees(c.estimated_employee_min, c.estimated_employee_max)}
                    <div>
                      <Badge value={c.headcount_status} />
                    </div>
                  </td>
                  <td>
                    <Badge value={c.qualification_status} />
                  </td>
                  <td className="small">
                    <Badge
                      value={c.source_count >= 2 ? "multi-source" : c.source_count === 1 ? "verified" : "unknown"}
                      label={`${c.source_count} source${c.source_count === 1 ? "" : "s"}`}
                      title={c.source_ids.join(", ")}
                    />
                    {c.multi_source_fields > 0 && (
                      <Badge value="multi-source" label={`${c.multi_source_fields} confirmed`} />
                    )}
                    {c.conflict_fields > 0 && <Badge value="conflicting" label={`${c.conflict_fields} conflict`} />}
                    {c.website_enriched && <Badge value="updated" label="web ✓" title="Enriched from company website" />}
                    {c.open_positions !== null && (
                      <Badge value="estimated" label={`hiring ${c.open_positions}`} title="Open positions on careers page (estimated)" />
                    )}
                    {c.founder_signal && <Badge value="estimated" label="founder" title="Founder mentioned on about/team page" />}
                    {c.family_business_signal && (
                      <Badge value="estimated" label="family" title="Self-described family business" />
                    )}
                  </td>
                  <td className="small">
                    <Badge value={c.freshness} />
                    <div className="muted">{fmtDate(c.last_verified_at)}</div>
                  </td>
                  <td className="small">
                    <span className="completeness" title={`${c.completeness}% of key fields`}>
                      <span style={{ width: `${c.completeness}%` }} />
                    </span>{" "}
                    {c.completeness}%
                  </td>
                  <td>
                    <Badge value={c.review_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items.length === 0 && <p className="muted">No companies match these filters.</p>}
        {pages > 1 && (
          <div className="pagination">
            {data.page > 1 && <Link href={pageHref(sp, data.page - 1)}>← Prev</Link>}
            <span className="muted">
              Page {data.page} / {pages}
            </span>
            {data.page < pages && <Link href={pageHref(sp, data.page + 1)}>Next →</Link>}
          </div>
        )}
      </section>
    </>
  );
}
