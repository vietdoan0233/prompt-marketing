import Link from "next/link";

import { Badge } from "@/components/Badge";
import { EmptyState, PageHeader } from "@/components/ui";
import { apiGet, fmtDate, fmtEmployees } from "@/lib/api";
import type { CompanyPage, SectorOption } from "@/lib/types";

import styles from "./companies.module.css";

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

/** Headcount confidence (from the employees facts) as a short sub-line; tone flags anything short of verified. */
const HEADCOUNT: Record<string, { label: string; tone?: string }> = {
  verified: { label: "verified headcount" },
  "multi-source": { label: "multi-source headcount", tone: "accent" },
  conflicting: { label: "conflicting headcount", tone: "bad" },
  estimated: { label: "estimated headcount", tone: "warn" },
  old: { label: "old headcount", tone: "warn" },
  "manually-corrected": { label: "manually corrected", tone: "info" },
  unknown: { label: "no headcount fact" },
};

const FRESHNESS_TONE: Record<string, string> = { aging: "warn", stale: "bad" };
const REVIEW_TONE: Record<string, string> = { needs_correction: "warn" };

function sentence(value: string): string {
  const text = value.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function toneText(tone: string | undefined): string {
  return tone ? `tone-text-${tone}` : "";
}

export default async function CompaniesPage({ searchParams }: { searchParams: Promise<SP> }) {
  const sp = await searchParams;
  const minEmployees = sp.min_employees ?? "20";
  const sectorOptions = await apiGet<SectorOption[]>("/sectors");
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
  const activeSort = sp.sort ?? "legal_name";
  const arrow = (key: string) => (activeSort === key ? ((sp.order ?? "asc") === "asc" ? " ▲" : " ▼") : "");
  const sortLink = (key: string, label: string, title?: string) => (
    <Link href={sortHref(sp, key)} className={activeSort === key ? "sorted" : undefined} title={title}>
      {label}
      {arrow(key)}
    </Link>
  );
  const ariaSort = (key: string) =>
    activeSort === key ? ((sp.order ?? "asc") === "asc" ? "ascending" : "descending") : undefined;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));

  const scopeNote =
    minEmployees === "20"
      ? "Default view: ≥20 employees (reported FTE, primary viability proxy)"
      : minEmployees === "0"
        ? "Includes below-threshold and unknown headcount"
        : `≥${minEmployees} employees`;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Estonia database" }, { label: "Company register" }]}
        title="Company database"
        subtitle="Search Estonia’s company register. Filter by sector, scale, freshness and review status; every fact keeps its source trail, and uncertainty is shown, never hidden."
      />

      <form className={`panel filters ${styles.filterForm}`} method="get">
        <h2 className="filters-title">Find companies</h2>
        <label className={styles.fieldWide}>
          Search
          <input name="q" defaultValue={sp.q} placeholder="Legal or trading name" />
        </label>
        <label className={styles.fieldNarrow}>
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
        <label className={styles.fieldWide}>
          Sector · EMTAK division
          <select name="sector" defaultValue={sp.sector ?? ""}>
            <option value="">All sectors</option>
            {sectorOptions.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.count.toLocaleString("en")})
              </option>
            ))}
          </select>
        </label>
        <label className={styles.fieldMin}>
          Min employees
          <select name="min_employees" defaultValue={minEmployees}>
            <option value="0">All (incl. sub-scale & unknown)</option>
            <option value="20">≥ 20 (default)</option>
            <option value="50">≥ 50</option>
            <option value="100">≥ 100</option>
            <option value="250">≥ 250</option>
          </select>
        </label>
        <label className={styles.fieldMax}>
          Max employees
          <input name="max_employees" type="number" min={0} defaultValue={sp.max_employees} placeholder="Any" />
        </label>
        <label className={styles.field}>
          Qualification
          <select name="qualification" defaultValue={sp.qualification ?? ""}>
            <option value="">Any status</option>
            <option value="qualified">≥20 qualified</option>
            <option value="borderline">borderline</option>
            <option value="below_threshold">below 20</option>
            <option value="sub_scale">sub-scale (1–2)</option>
            <option value="unknown_headcount">headcount unknown</option>
          </select>
        </label>
        <label className={styles.field}>
          Review
          <select name="review_status" defaultValue={sp.review_status ?? ""}>
            <option value="">All reviews</option>
            <option value="unreviewed">unreviewed</option>
            <option value="reviewed">reviewed</option>
            <option value="needs_correction">needs correction</option>
          </select>
        </label>
        <label className={styles.field}>
          Freshness
          <select name="freshness" defaultValue={sp.freshness ?? ""}>
            <option value="">Any age</option>
            <option value="fresh">fresh</option>
            <option value="aging">aging</option>
            <option value="stale">stale</option>
          </select>
        </label>
        <input type="hidden" name="sort" value={sp.sort ?? ""} />
        <input type="hidden" name="order" value={sp.order ?? ""} />
        <div className="filters-actions">
          <Link href="/companies" className="btn">
            Reset
          </Link>
          <button className="btn btn-primary">Apply</button>
        </div>
      </form>

      <div className={styles.resultsHead}>
        <span className={styles.resultsCount}>
          {data.total.toLocaleString("en")} {data.total === 1 ? "company matches" : "companies match"}
        </span>
        <span className={styles.resultsNote}>
          {scopeNote} · {data.page_size} per page
          {activeSort === "sector" &&
            " · Sector order: two-digit EMTAK division of the source-backed industry code; companies without a usable code last"}
        </span>
      </div>

      <div className="table-wrap">
        <table className={styles.table}>
          <thead>
            <tr>
              <th aria-sort={ariaSort("legal_name")}>{sortLink("legal_name", "Company")}</th>
              <th aria-sort={ariaSort("country")}>{sortLink("country", "Country")}</th>
              <th aria-sort={ariaSort("sector")}>
                {sortLink(
                  "sector",
                  "Sector (EMTAK)",
                  "Sorted by the two-digit EMTAK division of the source-backed industry code; companies without a usable code sort last in either order",
                )}
              </th>
              <th aria-sort={ariaSort("employees")}>{sortLink("employees", "Employees")}</th>
              <th aria-sort={ariaSort("qualification")}>{sortLink("qualification", "Qualification")}</th>
              <th>Provenance</th>
              <th aria-sort={ariaSort("last_verified_at")}>{sortLink("last_verified_at", "Freshness")}</th>
              <th aria-sort={ariaSort("completeness")}>
                {sortLink("completeness", "Complete", "Share of key profile fields that are populated")}
              </th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {data.items.length === 0 && (
              <tr>
                <td colSpan={9}>
                  {data.total === 0 ? (
                    <EmptyState>
                      No companies match these filters. Widen or reset them to return to the default view.
                    </EmptyState>
                  ) : (
                    <EmptyState>
                      Page {data.page.toLocaleString("en")} is past the end of these results ({pages.toLocaleString("en")}{" "}
                      {pages === 1 ? "page" : "pages"}). <Link href={pageHref(sp, 1)}>Go to the first page</Link>.
                    </EmptyState>
                  )}
                </td>
              </tr>
            )}
            {data.items.map((c) => {
              const headcount = HEADCOUNT[c.headcount_status] ?? { label: c.headcount_status };
              const employees = fmtEmployees(c.estimated_employee_min, c.estimated_employee_max);
              const hasSignals =
                c.multi_source_fields > 0 ||
                c.conflict_fields > 0 ||
                c.website_enriched ||
                c.open_positions !== null ||
                c.founder_signal ||
                c.family_business_signal;
              return (
                <tr key={c.id} className={c.qualification_status === "sub_scale" ? "row-muted" : ""}>
                  <td className={styles.companyCol}>
                    <Link href={`/companies/${c.id}`}>
                      <span className={styles.name}>{c.legal_name}</span>
                    </Link>
                    <span className="sub">
                      {c.registry_id ? <span className="mono">{c.registry_id}</span> : <em>no registry ID</em>}
                      {c.city ? ` · ${c.city}` : ""}
                    </span>
                  </td>
                  <td>{c.country}</td>
                  <td className={styles.sectorCol}>
                    {c.industry_code_details.length > 0 ? (
                      c.industry_code_details.map((item) => (
                        <span className={styles.code} key={`${item.code_system}:${item.code_version}:${item.code}`}>
                          <span className={`mono ${styles.primary}`}>{item.code}</span>
                          <span className="sub">
                            {item.code_system ?? "code system unknown"} · {item.code_version ?? "version unknown"}
                          </span>
                        </span>
                      ))
                    ) : c.industry_codes.length > 0 ? (
                      c.industry_codes.map((code) => (
                        <span className={styles.code} key={code}>
                          <span className={`mono ${styles.primary}`}>{code}</span>
                          <span className="sub">code system and version unknown</span>
                        </span>
                      ))
                    ) : (
                      <>
                        <span className="muted">—</span>
                        <span className="sub">no industry code</span>
                      </>
                    )}
                    {c.sector && <span className="sub">sector field: {c.sector}</span>}
                  </td>
                  <td className={styles.nowrap}>
                    <span className={employees === "unknown" ? "muted" : styles.primary}>{employees}</span>
                    <span
                      className={`sub ${toneText(headcount.tone)}`}
                      title={`Headcount confidence: ${c.headcount_status}`}
                    >
                      {headcount.label}
                    </span>
                  </td>
                  <td>
                    <Badge value={c.qualification_status} />
                  </td>
                  <td className={styles.provCol}>
                    <span className={c.source_count === 0 ? "muted" : styles.primary}>
                      {c.source_count} source{c.source_count === 1 ? "" : "s"}
                    </span>
                    {c.source_ids.length > 0 && (
                      <span className="sub" title={c.source_ids.join(", ")}>
                        {c.source_ids.join(" · ")}
                      </span>
                    )}
                    {hasSignals && (
                      <div className={styles.signals}>
                        {c.multi_source_fields > 0 && (
                          <Badge value="multi-source" label={`${c.multi_source_fields} confirmed`} />
                        )}
                        {c.conflict_fields > 0 && <Badge value="conflicting" label={`${c.conflict_fields} conflict`} />}
                        {c.website_enriched && <Badge value="updated" label="web ✓" title="Enriched from company website" />}
                        {c.open_positions !== null && (
                          <Badge
                            value="estimated"
                            label={`hiring ${c.open_positions}`}
                            title="Open positions on careers page (estimated)"
                          />
                        )}
                        {c.founder_signal && (
                          <Badge value="estimated" label="founder" title="Founder mentioned on about/team page" />
                        )}
                        {c.family_business_signal && (
                          <Badge value="estimated" label="family" title="Self-described family business" />
                        )}
                      </div>
                    )}
                  </td>
                  <td className={styles.nowrap}>
                    <span className={toneText(FRESHNESS_TONE[c.freshness]) || styles.primary} title={c.freshness}>
                      {sentence(c.freshness)}
                    </span>
                    <span className="sub">
                      {c.last_verified_at ? `last verified ${fmtDate(c.last_verified_at)}` : "no verification date"}
                    </span>
                  </td>
                  <td className={styles.nowrap}>
                    <span className="completeness" title={`${c.completeness}% of key fields`}>
                      <span style={{ width: `${c.completeness}%` }} />
                    </span>
                    <span className={styles.pct}>{c.completeness}%</span>
                  </td>
                  <td className={styles.nowrap}>
                    <span className={toneText(REVIEW_TONE[c.review_status]) || styles.primary} title={c.review_status}>
                      {sentence(c.review_status)}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <nav className="pagination" aria-label="Pagination">
          {data.page > 1 ? (
            <Link href={pageHref(sp, data.page - 1)} className={`btn ${styles.pageBtn}`}>
              ‹ Previous
            </Link>
          ) : (
            <span className={`btn ${styles.pageBtn}`} aria-disabled="true">
              ‹ Previous
            </span>
          )}
          <span className="muted">
            Page {data.page.toLocaleString("en")} of {pages.toLocaleString("en")}
          </span>
          {data.page < pages ? (
            <Link href={pageHref(sp, data.page + 1)} className={`btn ${styles.pageBtn}`}>
              Next ›
            </Link>
          ) : (
            <span className={`btn ${styles.pageBtn}`} aria-disabled="true">
              Next ›
            </span>
          )}
        </nav>
      )}
    </>
  );
}
