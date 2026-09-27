import Link from "next/link";

import { Badge } from "@/components/Badge";
import { PageHeader } from "@/components/ui";
import { apiGet, apiGetOrNotFound, fmtEmployees } from "@/lib/api";
import type { CompanyDetail, Source } from "@/lib/types";

import { ReviewControls, RunDecayCheck } from "./ClientControls";
import { ContactsTab } from "./ContactsTab";
import { DecayTab } from "./DecayTab";
import { OwnershipTab } from "./OwnershipTab";
import { ProfileTab } from "./ProfileTab";
import { ReviewTab } from "./ReviewTab";
import styles from "./company.module.css";
import { TAB_KEYS, type TabKey } from "./shared";

export const dynamic = "force-dynamic";

const TABS: Record<TabKey, { label: string; subtitle: string }> = {
  profile: {
    label: "Profile & financials",
    subtitle:
      "Company profile · source-backed annual figures shown with filing period, statement scope and calculation type.",
  },
  ownership: {
    label: "Ownership & identity",
    subtitle:
      "Share capital and current shareholders from the register, stable identity keys and the registered seat.",
  },
  decay: {
    label: "Digital decay",
    subtitle:
      "Opt-in website activity check · each check shown with what was observed, the rule applied and its evidence.",
  },
  review: {
    label: "Review & correction",
    subtitle: "Current source facts, additive manual corrections, the evidence timeline and the audit history.",
  },
  contacts: {
    label: "Contacts & privacy",
    subtitle: "Source-backed contacts only, handled as personal data with a recorded GDPR erasure path.",
  },
};

function isTab(v: unknown): v is TabKey {
  return typeof v === "string" && (TAB_KEYS as readonly string[]).includes(v);
}

export default async function CompanyPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const tab: TabKey = isTab(sp.tab) ? sp.tab : "profile";

  const d = await apiGetOrNotFound<CompanyDetail>(`/companies/${encodeURIComponent(id)}`);
  const c = d.company;

  // Only needed to label a contact's source as approved; skipped when there is nothing to label.
  let sources: Source[] | null = null;
  if (tab === "contacts" && d.contacts.length > 0) {
    sources = await apiGet<Source[]>("/sources").catch(() => null);
  }

  const emtak = c.industry_code_details[0] ?? null;
  const sectorText =
    c.sector ??
    (emtak
      ? `EMTAK ${emtak.code}${emtak.code_version ? ` (${emtak.code_version})` : ""}`
      : c.industry_codes[0]
        ? `EMTAK ${c.industry_codes[0]}`
        : "sector unknown");

  const counts: Partial<Record<TabKey, number>> = {
    ownership: d.shareholders.length,
    review: d.audit_events.length,
    contacts: d.contacts.length,
  };

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Company database", href: "/companies" }, { label: TABS[tab].label }]}
        title={c.legal_name}
        subtitle={TABS[tab].subtitle}
        meta={
          <>
            <span className="ident mono">{c.registry_id ?? "registry id unknown"}</span>
            <span className="dot">·</span>
            <span>
              {c.city ?? "city unknown"}, {c.country}
            </span>
            <span className="dot">·</span>
            <span>{sectorText}</span>
            <span className="dot">·</span>
            <span>{fmtEmployees(c.estimated_employee_min, c.estimated_employee_max)} employees</span>
            <span className={styles.metaBadges}>
              <Badge
                value={c.registry_status ?? "unknown"}
                label={`Registry status: ${c.registry_status ?? "unknown"}`}
                title="Official registry status code as supplied by the register"
                tone={c.registry_status ? "accent" : "muted"}
              />
              <Badge value={c.qualification_status} />
              <Badge value={c.headcount_status} label={`headcount ${c.headcount_status}`} />
              <Badge value={c.freshness} />
              <Badge value={c.review_status} />
            </span>
          </>
        }
        actions={
          <div className={styles.headActions}>
            <ReviewControls companyId={c.id} status={c.review_status} />
            {tab === "decay" && <RunDecayCheck companyId={c.id} hasResult={!!d.digital_decay?.signal} />}
          </div>
        }
      />

      {d.merged_into_id && (
        <div className="notice notice-info">
          This record was merged into <Link href={`/companies/${d.merged_into_id}`}>the surviving company</Link>.
        </div>
      )}
      {d.warnings.length > 0 && (
        <div className="notice">
          <strong>Data-quality warnings</strong>
          <ul>
            {d.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      <nav className={`tabs ${styles.tabs}`} aria-label="Company sections">
        {TAB_KEYS.map((k) => (
          <Link
            key={k}
            href={`/companies/${c.id}?tab=${k}`}
            className={k === tab ? "active" : undefined}
            aria-current={k === tab ? "page" : undefined}
            scroll={false}
          >
            {TABS[k].label}
            {counts[k] !== undefined && <span className="tab-count">{counts[k]}</span>}
          </Link>
        ))}
      </nav>

      {tab === "profile" && <ProfileTab d={d} />}
      {tab === "ownership" && <OwnershipTab d={d} />}
      {tab === "decay" && <DecayTab view={d.digital_decay} />}
      {tab === "review" && <ReviewTab d={d} />}
      {tab === "contacts" && <ContactsTab d={d} sources={sources} />}
    </>
  );
}
