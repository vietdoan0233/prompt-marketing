import Link from "next/link";

import { PageHeader, Panel } from "@/components/ui";
import { apiGet } from "@/lib/api";
import type { CompanyDetail, Duplicate } from "@/lib/types";

import { DuplicateReview, REVIEW_CRUMBS, REVIEW_SUBTITLE, REVIEW_TITLE } from "../../DuplicateReview";

export const dynamic = "force-dynamic";

export default async function DuplicateReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  // There is no single-candidate endpoint: read the full list (every status) and pick this one.
  const all = await apiGet<Duplicate[]>("/quality/duplicates", { status: "all" });
  const d = all.find((x) => x.id === id);

  if (!d) {
    return (
      <>
        <PageHeader crumbs={REVIEW_CRUMBS} title={REVIEW_TITLE} subtitle={REVIEW_SUBTITLE} />
        <Panel title="Candidate not found">
          <p className="notice notice-info">
            No duplicate candidate with ID <code>{id}</code> was found. Check the link, or return to the candidate list.
          </p>
          <Link href="/quality#duplicates">← Back to duplicate candidates</Link>
        </Panel>
      </>
    );
  }

  // A company that fails to load is shown as a notice in its card rather than failing the whole page.
  const load = (companyId: string) =>
    apiGet<CompanyDetail>(`/companies/${companyId}`).catch(() => null as CompanyDetail | null);
  const [a, b] = await Promise.all([load(d.company_a_id), load(d.company_b_id)]);

  return <DuplicateReview d={d} a={a} b={b} />;
}
