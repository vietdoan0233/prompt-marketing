"""Digital Decay CLI: opt-in website activity check for Estonian companies.

    python -m app.decay --name "Foo OÜ" [--address "Pärnu mnt 10, 10148 Tallinn"] [--domain foo.ee]
    python -m app.decay --registry-code 12345678 [--domain foo.ee]
    python -m app.decay --company-id <uuid>
    python -m app.decay --revenue-min 4000000 --revenue-max 6000000 [--limit 15] [--min-employees 20]
    python -m app.decay --cash-harvesting [--limit 100] [--recheck-after-days 30]
    python -m app.decay --enable-source (alias --approve-and-enable) | --disable-source | --status
    python -m app.decay --sync-register-domains [--from-cache]   (one-time: register-declared domains)

The source is disabled by default and live network access needs LIVE_CONNECTORS_ENABLED=true.
"""

import argparse
import json
import re
import sys
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.connectors.base import ConnectorError
from app.db import SessionLocal
from app.models import Company, CompanyIdentifier, IngestionRun, Source, utcnow
from app.services import digital_decay as dd
from app.services.permissions import PermissionDenied, ingestion_gate
from app.services.seller_funnel import is_registered_status, seller_funnel
from app.services.source_registry import sync_sources

ACTOR = "system:decay"
DASH = "—"
# The seller-prospect route's default size band; it only affects next-action ordering, not candidacy.
CASH_HARVESTING_REVENUE_BAND = (5_000_000, 50_000_000)


def _parser() -> argparse.ArgumentParser:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m app.decay", description=__doc__.split("\n\n")[0])
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="legal name (matched against the database)")
    target.add_argument("--registry-code", help="8-digit Estonian registry code")
    target.add_argument("--company-id", help="company id in the database")
    target.add_argument("--revenue-min", type=int, help="batch: latest reported revenue lower bound (EUR)")
    target.add_argument(
        "--cash-harvesting",
        action="store_true",
        help="batch: Cash Harvesting candidates from the seller-prospect funnel (still registered)",
    )
    target.add_argument(
        "--enable-source",
        "--approve-and-enable",
        dest="enable_source",
        action="store_true",
        help="enable the web-digital-decay source",
    )
    target.add_argument("--disable-source", action="store_true", help="disable the web-digital-decay source")
    target.add_argument("--status", action="store_true", help="show source status and gate reasons")
    target.add_argument(
        "--sync-register-domains",
        action="store_true",
        help="import company-declared website/email domains from the register's general-data file",
    )
    p.add_argument(
        "--from-cache",
        action="store_true",
        help="with --sync-register-domains: use the verified cached file only (no download)",
    )
    p.add_argument("--address", help="registered address, used to disambiguate --name")
    p.add_argument("--domain", help="website domain override (still verified by the connector)")
    p.add_argument("--revenue-max", type=int, default=2**62, help="batch: revenue upper bound (EUR)")
    p.add_argument("--limit", type=int, default=15, help="batch: maximum number of companies")
    p.add_argument(
        "--recheck-after-days",
        type=int,
        default=30,
        help="with --cash-harvesting: skip companies checked within this many days (0 = recheck all)",
    )
    p.add_argument("--min-employees", type=int, default=settings.min_employees_default)
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--actor", default=ACTOR)
    return p


def _source(session: Session) -> Source | None:
    return session.get(Source, dd.SOURCE_ID)


def _print_status(session: Session) -> None:
    source = _source(session)
    if source is None:
        print(f"{dd.SOURCE_ID}: not registered")
        return
    reasons = ingestion_gate(source, live=True)
    print(f"source            {source.id}")
    print(f"enabled           {source.enabled}")
    print(f"permission_status {source.permission_status}")
    print(f"live connectors   {get_settings().live_connectors_enabled}")
    if reasons:
        print("gate              denied")
        for r in reasons:
            print(f"  - {r}")
    else:
        print("gate              open")


def _company_by_registry_code(session: Session, code: str) -> Company | None:
    code = code.strip().upper().removeprefix("EE:").replace(" ", "")
    ident = session.scalar(
        select(CompanyIdentifier).where(
            CompanyIdentifier.kind == "registry_id", CompanyIdentifier.value == f"EE:{code}"
        )
    )
    return ident.company if ident else None


def _fmt_revenue(rev: dict[str, Any] | None) -> str:
    if not rev:
        return DASH
    return f"€{rev['amount'] / 1e6:.1f}M FY{rev['fiscal_year']}"


def _cell(value: Any) -> str:
    return DASH if value is None or value == "" else str(value)


def _fte_change(checks: dict[str, Any]) -> str:
    pct = (checks.get("headcount") or {}).get("change_pct")
    return f"{pct:+.1f}%" if isinstance(pct, int | float) else DASH


def _row(session: Session, company: Company, signal: dict[str, Any] | None) -> list[str]:
    checks = (signal or {}).get("checks") or {}
    copyright_ = checks.get("copyright") or {}
    news = checks.get("news") or {}
    hiring = checks.get("hiring") or {}
    domain = (signal or {}).get("domain")
    domain_txt = f"{domain} ({signal.get('domain_verification')})" if signal and domain else DASH
    roles = hiring.get("open_roles")
    if roles is None and hiring.get("state") == "zero_roles":
        roles = 0
    revenue = (signal or {}).get("revenue") or dd.latest_revenue(session, company.id)
    return [
        _cell(dd.registry_code(session, company.id)),
        company.legal_name[:30],
        _fmt_revenue(revenue),
        domain_txt,
        _cell(copyright_.get("year")),
        _cell(news.get("latest_date")),
        _cell(news.get("posts_18m")),
        _cell(roles),
        _fte_change(checks),
        _cell((signal or {}).get("verdict")),
    ]


def _print_table(rows: list[list[str]]) -> None:
    header = [
        "REGISTRY",
        "NAME",
        "REVENUE",
        "DOMAIN (verification)",
        "©",
        "LATEST NEWS",
        "POSTS18M",
        "ROLES",
        "FTE Δ",
        "VERDICT",
    ]
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for r in [header, *rows]:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip())


def _report(session: Session, run: IngestionRun, companies: list[Company], as_json: bool) -> None:
    c = run.counts or {}
    summary = (
        f"run {run.id} {run.status} discovered={c.get('discovered', 0)} accepted={c.get('accepted', 0)} "
        f"updated={c.get('updated', 0)} unchanged={c.get('unchanged', 0)} rejected={c.get('rejected', 0)}"
    )
    print(summary, file=sys.stderr if as_json else sys.stdout)
    for err in run.errors or []:
        print(f"  error [{err.get('stage')}] {err.get('message')}", file=sys.stderr)
    out: list[dict[str, Any]] = []
    rows: list[list[str]] = []
    for company in companies:
        fact = dd.latest_signal(session, company.id)
        signal = fact.value_json if fact is not None and isinstance(fact.value_json, dict) else None
        if as_json:
            out.append(
                {
                    "company_id": company.id,
                    "registry_code": dd.registry_code(session, company.id),
                    "legal_name": company.legal_name,
                    "run_id": run.id,
                    "signal": signal,
                }
            )
        else:
            rows.append(_row(session, company, signal))
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif rows:
        _print_table(rows)


def _run(session: Session, companies: list[Company], args: argparse.Namespace) -> None:
    domains = {companies[0].id: args.domain} if args.domain and len(companies) == 1 else None
    if len(companies) > 1:
        print(f"checking {len(companies)} companies (this can take several minutes)...", file=sys.stderr)
    run = dd.run_decay(session, companies, actor=args.actor, domains=domains)
    _report(session, run, companies, args.json)


def _cash_harvesting_batch(session: Session, limit: int, recheck_after_days: int) -> list[Company]:
    """Registered Cash Harvesting candidates in seller-funnel order, skipping recently checked ones."""
    funnel = seller_funnel(
        session,
        min_revenue_eur=CASH_HARVESTING_REVENUE_BAND[0],
        max_revenue_eur=CASH_HARVESTING_REVENUE_BAND[1],
        sector=None,
        limit=10**9,
        view="cash_harvesting",
    )
    cutoff = utcnow() - timedelta(days=recheck_after_days) if recheck_after_days > 0 else None
    picked: list[Company] = []
    cap = max(0, min(limit, get_settings().decay_batch_limit_max))
    for item in funnel.items:
        if len(picked) >= cap:
            break
        if not is_registered_status(item.registry_status):
            continue
        if cutoff is not None:
            fact = dd.latest_signal(session, item.company_id)
            if fact is not None:
                # SQLite hands back naive UTC; utcnow() is aware.
                observed = (
                    fact.observed_at if fact.observed_at.tzinfo else fact.observed_at.replace(tzinfo=UTC)
                )
                if observed >= cutoff:
                    continue
        company = session.get(Company, item.company_id)
        if company is not None:
            picked.append(company)
    return picked


def _unregistered(session: Session, args: argparse.Namespace) -> None:
    postal = re.search(r"\b\d{5}\b", args.address or "")
    target = {
        "company_id": None,
        "legal_name": args.name,
        "registry_code": None,
        "postal_code": postal.group(0) if postal else None,
        "street": args.address,
        "address": args.address,
        "domain": args.domain,
        "revenue": None,
        "headcount": None,
    }
    signal = dd.run_unregistered(session, target, actor=args.actor)
    print("not stored: company is not in the database; revenue unknown", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "company_id": None,
                        "registry_code": None,
                        "legal_name": args.name,
                        "run_id": None,
                        "signal": signal,
                    }
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    checks = signal.get("checks") or {}
    domain = signal.get("domain")
    row = [
        DASH,
        str(args.name)[:30],
        DASH,
        f"{domain} ({signal.get('domain_verification')})" if domain else DASH,
        _cell((checks.get("copyright") or {}).get("year")),
        _cell((checks.get("news") or {}).get("latest_date")),
        _cell((checks.get("news") or {}).get("posts_18m")),
        _cell((checks.get("hiring") or {}).get("open_roles")),
        _fte_change(checks),
        _cell(signal.get("verdict")),
    ]
    _print_table([row])


def _sync_register_domains(session: Session, args: argparse.Namespace) -> None:
    from app.services.ee_register_domains import sync_register_domains

    run = sync_register_domains(session, actor=args.actor, live=not args.from_cache)
    c = run.counts or {}
    print(
        f"run {run.id} {run.status} discovered={c.get('discovered', 0)} accepted={c.get('accepted', 0)} "
        f"updated={c.get('updated', 0)} unchanged={c.get('unchanged', 0)} rejected={c.get('rejected', 0)} "
        f"facts_added={c.get('facts_added', 0)} facts_changed={c.get('facts_changed', 0)} "
        f"register_entries_matched={c.get('register_entries_matched', 0)} "
        f"companies_with_www={c.get('companies_with_www', 0)} "
        f"companies_with_email_domain={c.get('companies_with_email_domain', 0)}"
    )
    for err in run.errors or []:
        print(f"  error [{err.get('stage')}] {err.get('message')}", file=sys.stderr)
    if run.status != "UPSERTED":
        sys.exit(1)


def main() -> None:
    args = _parser().parse_args()
    with SessionLocal() as session:
        sync_sources(session, actor=ACTOR)
        session.commit()
        try:
            if args.status:
                _print_status(session)
                return
            if args.sync_register_domains:
                _sync_register_domains(session, args)
                return
            if args.enable_source or args.disable_source:
                reasons = dd.set_enabled(session, enabled=bool(args.enable_source), actor=args.actor)
                if reasons:
                    sys.exit(f"{dd.SOURCE_ID} cannot be enabled: {'; '.join(reasons)}")
                print(f"{dd.SOURCE_ID} {'enabled' if args.enable_source else 'disabled'}")
                _print_status(session)
                return
            if args.company_id:
                company = session.get(Company, args.company_id)
                if company is None:
                    sys.exit(f"unknown company id {args.company_id}")
                _run(session, [company], args)
                return
            if args.registry_code:
                company = _company_by_registry_code(session, args.registry_code)
                if company is None:
                    print(
                        f"no company with registry code {args.registry_code} in the database", file=sys.stderr
                    )
                    sys.exit(2)
                _run(session, [company], args)
                return
            if args.name:
                matches = dd.find_companies(session, args.name, args.address)
                if not matches:
                    _unregistered(session, args)
                    return
                if len(matches) > 1:
                    print(
                        f"{len(matches)} companies match '{args.name}'; narrow with --address or use "
                        "--registry-code:",
                        file=sys.stderr,
                    )
                    for c in matches:
                        addr = dd.current_address(session, c.id) or {}
                        where = ", ".join(
                            v
                            for v in (addr.get("address_line"), addr.get("postal_code"), addr.get("city"))
                            if v
                        )
                        print(
                            f"  {_cell(dd.registry_code(session, c.id))}  {c.legal_name}  {where or DASH}",
                            file=sys.stderr,
                        )
                    sys.exit(2)
                _run(session, matches, args)
                return
            if args.cash_harvesting:
                companies = _cash_harvesting_batch(session, args.limit, args.recheck_after_days)
                if not companies:
                    print("no unchecked, registered Cash Harvesting candidates", file=sys.stderr)
                    return
                _run(session, companies, args)
                return
            # batch by revenue band
            companies = dd.select_batch(
                session, args.revenue_min, args.revenue_max, args.limit, args.min_employees
            )
            if not companies:
                print("no companies match the revenue band and headcount filter", file=sys.stderr)
                return
            _run(session, companies, args)
        except PermissionDenied as exc:
            sys.exit(f"Digital Decay check rejected: {'; '.join(exc.reasons)}")
        except ConnectorError as exc:
            sys.exit(f"Digital Decay check failed: {exc}")


if __name__ == "__main__":
    main()
