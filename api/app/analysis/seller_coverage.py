"""Read-only coverage report for the seller prospect funnel.

Answers from the imported official files only: which financial fields each fiscal year actually carries,
how many companies reach three comparable years, where the provisional size band cuts, which EMTAK peer
groups are large enough for an index, and how often the group-parent and holding flags occur.

Run from api/:
    python -m app.analysis.seller_coverage [--min-revenue 5000000] [--max-revenue 50000000] [--json]
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, CompanyFact, CompanyFinancial
from app.services.seller_funnel import MIN_PEERS, seller_funnel

FIELDS = (
    "revenue",
    "operating_profit",
    "total_assets",
    "equity",
    "employees_fte",
    "net_income",
    "profit_before_tax",
    "depreciation_and_impairment",
    "labour_cost",
    "ebitda",
)
REVENUE_BUCKETS = (
    (0.0, 2e6, "<€2m"),
    (2e6, 5e6, "€2–5m"),
    (5e6, 10e6, "€5–10m"),
    (10e6, 20e6, "€10–20m"),
    (20e6, 50e6, "€20–50m"),
    (50e6, 200e6, "€50–200m"),
    (200e6, float("inf"), "≥€200m"),
)


def _bucket(value: float) -> str:
    for low, high, label in REVENUE_BUCKETS:
        if low <= value < high:
            return label
    return "negative"


def coverage(session: Session, *, min_revenue_eur: int, max_revenue_eur: int) -> dict[str, Any]:
    in_scope = select(Company.id).where(Company.country == "EE", Company.merged_into_id.is_(None))

    statuses: Counter[str] = Counter()
    for fact in session.scalars(
        select(CompanyFact).where(
            CompanyFact.company_id.in_(in_scope),
            CompanyFact.field_name == "registry_status",
            CompanyFact.valid_to.is_(None),
        )
    ):
        statuses[str(fact.value_json)] += 1

    rows_by_year: dict[str, Counter[str]] = defaultdict(Counter)
    period_days: Counter[str] = Counter()
    for row in session.scalars(
        select(CompanyFinancial).where(
            CompanyFinancial.company_id.in_(in_scope), CompanyFinancial.review_status != "superseded"
        )
    ):
        key = f"{row.fiscal_year} {row.statement_scope or 'unproven'}"
        counts = rows_by_year[key]
        counts["rows"] += 1
        for name in FIELDS:
            if getattr(row, name) is not None:
                counts[name] += 1
        if row.ebitda is not None and row.value_type == "derived":
            counts["ebitda_derived"] += 1
        if row.period_start and row.period_end:
            days = (row.period_end - row.period_start).days + 1
            period_days["12 months" if 330 <= days <= 400 else "other length"] += 1
        else:
            period_days["dates missing"] += 1

    funnel = seller_funnel(
        session, min_revenue_eur=min_revenue_eur, max_revenue_eur=max_revenue_eur, sector=None, limit=10**9
    )
    items = funnel.items
    registered = [item for item in items if item.registry_status in {"Registrisse kantud", "Registered"}]
    revenue_buckets = Counter(
        _bucket(item.latest_revenue_eur) for item in registered if item.latest_revenue_eur is not None
    )
    in_band = [item for item in registered if item.focus_band == "core"]
    evidence_gaps = Counter(
        re.sub(r"\b(19|20)\d{2}\b", "YYYY", issue)
        for item in in_band
        if item.evidence_status != "complete"
        for issue in item.issues[:1]
    )
    eligible = [
        item
        for item in in_band
        if item.evidence_status == "complete" and "holding_activity" not in item.flags
    ]
    group_sizes = Counter(item.peer_group or "none" for item in eligible)
    indexed = sum(count for group, count in group_sizes.items() if group != "none" and count >= MIN_PEERS)
    return {
        "companies": len(items),
        "registry_status": dict(statuses.most_common()),
        "financial_rows_by_year_and_scope": {key: dict(rows_by_year[key]) for key in sorted(rows_by_year)},
        "fiscal_period_lengths": dict(period_days),
        "latest_comparable_revenue_registered": {
            label: revenue_buckets.get(label, 0) for *_, label in REVENUE_BUCKETS
        },
        "band": {"min_revenue_eur": min_revenue_eur, "max_revenue_eur": max_revenue_eur},
        "funnel_stages": {stage.key: stage.count for stage in funnel.stages},
        "why_in_band_companies_lack_complete_evidence": dict(evidence_gaps.most_common(10)),
        "flags": dict(Counter(flag for item in items for flag in item.flags)),
        "peer_groups_all_sizes": dict(group_sizes.most_common()),
        "peer_groups_indexed": len([g for g, c in group_sizes.items() if g != "none" and c >= MIN_PEERS]),
        "eligible_companies_with_index": indexed,
        "eligible_companies": len(eligible),
        "latest_comparable_year": dict(
            sorted(Counter(item.latest_year for item in registered if item.latest_year).items())
        ),
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Seller funnel coverage ({report['companies']} imported companies)", ""]
    lines += ["## Funnel stages", ""] + [f"- {k}: {v}" for k, v in report["funnel_stages"].items()]
    lines += ["", "## Registry status", ""] + [f"- {k}: {v}" for k, v in report["registry_status"].items()]
    lines += [
        "",
        "## Field coverage (rows with a value)",
        "",
        "| year scope | " + " | ".join(("rows", *FIELDS)),
    ]
    lines.append("|" + "---|" * (len(FIELDS) + 2))
    for key, counts in report["financial_rows_by_year_and_scope"].items():
        cells = [str(counts.get("rows", 0))] + [str(counts.get(name, 0)) for name in FIELDS]
        lines.append(f"| {key} | " + " | ".join(cells))
    lines += ["", f"Fiscal period lengths: {report['fiscal_period_lengths']}"]
    lines += ["", "## Latest comparable revenue (registered companies)", ""]
    lines += [f"- {k}: {v}" for k, v in report["latest_comparable_revenue_registered"].items()]
    lines += ["", "## Why in-band companies lack complete evidence (first issue)", ""]
    lines += [f"- {k}: {v}" for k, v in report["why_in_band_companies_lack_complete_evidence"].items()]
    lines += ["", f"## Peer groups (eligible companies: {report['eligible_companies']})", ""]
    lines.append(
        f"Groups with ≥{MIN_PEERS} peers: {report['peer_groups_indexed']}, covering "
        f"{report['eligible_companies_with_index']} of {report['eligible_companies']} eligible companies."
    )
    lines += [f"- EMTAK {k}: {v}" for k, v in report["peer_groups_all_sizes"].items()]
    lines += ["", f"Flags: {report['flags']}", f"Latest comparable year: {report['latest_comparable_year']}"]
    return "\n".join(lines)


def main() -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--min-revenue", type=int, default=5_000_000)
    parser.add_argument("--max-revenue", type=int, default=50_000_000)
    parser.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = parser.parse_args()
    with SessionLocal() as session:
        report = coverage(session, min_revenue_eur=args.min_revenue, max_revenue_eur=args.max_revenue)
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else to_markdown(report))


if __name__ == "__main__":
    main()
