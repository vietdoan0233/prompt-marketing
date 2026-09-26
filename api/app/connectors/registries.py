"""National registry connectors (A-tier sources).

Live and credential-free (verified 2026-09):
- BrregConnector (NO): Enhetsregisteret open API. Has registered headcount (antallAnsatte from the
  Aa-register), so discovery filters >= min_employees at the source (fraAntallAnsatte).
- PrhConnector (FI): PRH/YTJ open data API v3 (CC BY 4.0). Has NO headcount field; Finnish
  candidates stay "headcount unknown" until an approved headcount source is added.

Credential-gated (free registration; fail closed without credentials; response mapping follows the
published API documentation and has not been exercised live from this environment):
- ZefixConnector (CH): ZefixPublicREST (HTTP basic auth). No headcount field.
- CvrConnector (DK): CVR system-to-system Elasticsearch distribution (HTTP basic auth). Has
  annual/quarterly employment figures.

`fixture` in connector_config is for automated tests only; production sources set `live: true`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.connectors.http import PoliteClient
from app.domain.records import PARSER_VERSION


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class _Registry:
    def __init__(self, fixture_path: Path | None, live: bool, rate_limit_per_minute: int | None = 60) -> None:
        self.fixture_path = fixture_path
        self.live = live
        self.rate_limit = rate_limit_per_minute
        self._client: PoliteClient | None = None

    @property
    def client(self) -> PoliteClient:
        if self._client is None:
            self._client = PoliteClient(self.rate_limit)
        return self._client

    def _fixture(self) -> dict[str, Any]:
        if not self.fixture_path or not self.fixture_path.exists():
            raise ConnectorError(
                "connector is not live and has no fixture; set live: true and LIVE_CONNECTORS_ENABLED=true"
            )
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot:
        # Search responses already contain the full record; no second request per entity.
        return FetchedSnapshot(
            ref.reference, ref.hint["record"], ref.source_url, 200, ref.hint.get("request")
        )


class BrregConnector(_Registry):
    parser_version = f"brreg-{PARSER_VERSION}"
    api = "https://data.brreg.no/enhetsregisteret/api/enheter"
    public_url = "https://virksomhet.brreg.no/nb/oppslag/enheter/{orgnr}"

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        limit = int(query.get("max_records", 100))
        refs: list[CandidateRef] = []
        pages = [] if self.live else [self._fixture()]
        page = 0
        while True:
            if self.live:
                params: dict[str, Any] = {
                    "fraAntallAnsatte": min_employees,
                    "konkurs": "false",
                    "underAvvikling": "false",
                    "underTvangsavviklingEllerTvangsopplosning": "false",
                    "size": min(100, limit),
                    "page": page,
                }
                for key in ("naeringskode", "kommunenummer", "organisasjonsform"):
                    if query.get(key):
                        params[key] = query[key]
                if query.get("industry_code"):
                    params["naeringskode"] = query["industry_code"]
                data = self.client.get_json(self.api, params=params)
                request_id = f"{self.api}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
            else:
                if page >= len(pages):
                    break
                data, request_id = pages[page], f"fixture:{self.fixture_path}"
            entities = data.get("_embedded", {}).get("enheter", [])
            for e in entities:
                employees = e.get("antallAnsatte")
                if not query.get("include_below_threshold") and (
                    employees is None or employees < min_employees
                ):
                    continue
                code = (e.get("naeringskode1") or {}).get("kode", "")
                wanted = query.get("industry_code") or query.get("naeringskode")
                if not self.live and wanted and not code.startswith(str(wanted)):
                    continue
                orgnr = str(e["organisasjonsnummer"])
                e.setdefault("_observed_at", _now_iso())
                refs.append(
                    CandidateRef(
                        orgnr, self.public_url.format(orgnr=orgnr), {"record": e, "request": request_id}
                    )
                )
                if len(refs) >= limit:
                    return refs
            page += 1
            total_pages = (data.get("page") or {}).get("totalPages", 1)
            if not self.live or page >= total_pages or not entities:
                break
        return refs

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        e = snapshot.payload
        addr = e.get("forretningsadresse") or e.get("postadresse") or {}
        nace = e.get("naeringskode1") or {}
        employees = e.get("antallAnsatte")
        row: dict[str, Any] = {
            "source_key": f"NO:{e['organisasjonsnummer']}",
            "legal_name": e.get("navn"),
            "country": addr.get("landkode") or "NO",
            "city": (addr.get("poststed") or "").title() or None,
            "registry_id": str(e["organisasjonsnummer"]),
            "industry_code": nace.get("kode"),
            "sector": nace.get("beskrivelse"),
            "employees": str(employees) if employees is not None else None,
            "website": e.get("hjemmeside"),
            "source_url": snapshot.source_url,
            "observed_at": e.get("_observed_at"),
        }
        if e.get("registrertIMvaregisteret"):
            row["vat_id"] = f"NO{e['organisasjonsnummer']}MVA"
        return [row]


class PrhConnector(_Registry):
    parser_version = f"prh-{PARSER_VERSION}"
    api = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"
    public_url = "https://tietopalvelu.ytj.fi/yritys/{business_id}"

    @staticmethod
    def is_active(c: dict[str, Any]) -> bool:
        # tradeRegisterStatus "1" = registered; "4" = ceased. Also require a current (non-ended) name.
        if str(c.get("tradeRegisterStatus", "1")) != "1":
            return False
        return any(not n.get("endDate") for n in c.get("names", []))

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        limit = int(query.get("max_records", 100))
        max_pages = int(query.get("max_pages", 10))
        refs: list[CandidateRef] = []
        for page in range(1, max_pages + 1):
            if self.live:
                params = {
                    k: query[k]
                    for k in ("mainBusinessLine", "location", "companyForm", "postCode", "name")
                    if query.get(k)
                }
                if query.get("industry_code"):
                    params["mainBusinessLine"] = query["industry_code"]
                params["page"] = page
                data = self.client.get_json(self.api, params=params)
                request_id = f"{self.api}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
            else:
                if page > 1:
                    break
                data, request_id = self._fixture(), f"fixture:{self.fixture_path}"
            companies = data.get("companies", [])
            for c in companies:
                if not self.is_active(c):
                    continue
                code = (c.get("mainBusinessLine") or {}).get("type", "")
                if (
                    not self.live
                    and query.get("industry_code")
                    and not str(code).startswith(str(query["industry_code"]))
                ):
                    continue
                bid = c["businessId"]["value"]
                # PRH has no headcount: candidates are never size-filtered here (not guessed either).
                refs.append(
                    CandidateRef(
                        bid, self.public_url.format(business_id=bid), {"record": c, "request": request_id}
                    )
                )
                if len(refs) >= limit:
                    return refs
            if not companies or page * 100 >= int(data.get("totalResults", 0)):
                break
        return refs

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        c = snapshot.payload
        names = [x for x in c.get("names", []) if not x.get("endDate")]
        legal = next(
            (x["name"] for x in names if str(x.get("type")) == "1"), names[0]["name"] if names else None
        )
        trading = next((x["name"] for x in names if str(x.get("type")) == "3"), None)
        line = c.get("mainBusinessLine") or {}
        descriptions = {d.get("languageCode"): d.get("description") for d in line.get("descriptions", [])}
        city = None
        for a in c.get("addresses", []):
            offices = a.get("postOffices") or []
            fi = next(
                (o for o in offices if str(o.get("languageCode")) == "1"), offices[0] if offices else None
            )
            if fi:
                city = (fi.get("city") or "").title() or None
                break
        website = (c.get("website") or {}).get("url")
        return [
            {
                "source_key": f"FI:{c['businessId']['value']}",
                "legal_name": legal,
                "trading_name": trading,
                "country": "FI",
                "city": city,
                "registry_id": c["businessId"]["value"],
                "industry_code": line.get("type"),
                "sector": descriptions.get("3") or descriptions.get("1"),
                "website": website,
                "source_url": snapshot.source_url,
                "observed_at": c.get("lastModified"),
            }
        ]


class ZefixConnector(_Registry):
    """Switzerland. Requires ZEFIX_USERNAME/ZEFIX_PASSWORD (request access from the FOJ Zefix team)."""

    parser_version = f"zefix-{PARSER_VERSION}"
    api = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1"
    public_url = "https://www.zefix.ch/de/search/entity/list/firm/{ehraid}"

    def __init__(
        self, fixture_path: Path | None, live: bool, rate_limit_per_minute: int | None, auth: tuple[str, str]
    ):
        super().__init__(fixture_path, live, rate_limit_per_minute)
        self.auth = auth

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        if self.live and not all(self.auth):
            raise ConnectorError("Zefix credentials missing (ZEFIX_USERNAME/ZEFIX_PASSWORD); failing closed")
        if not query.get("name") or len(str(query["name"])) < 3:
            raise ConnectorError(
                "Zefix search needs a 'name' of at least 3 characters (no industry filter exists)"
            )
        body = {"name": query["name"], "activeOnly": True}
        if query.get("canton"):
            body["canton"] = query["canton"]
        if self.live:
            resp = self.client.request("POST", f"{self.api}/company/search", json=body, auth=self.auth)
            if resp.status >= 400:
                raise ConnectorError(f"Zefix search returned HTTP {resp.status}")
            results = json.loads(resp.text)
        else:
            results = self._fixture().get("companies", [])
        limit = int(query.get("max_records", 50))
        return [
            CandidateRef(
                str(c.get("uid")),
                self.public_url.format(ehraid=c.get("ehraid")),
                {"record": c, "request": "zefix:search"},
            )
            for c in results[:limit]
            if c.get("uid") and str(c.get("status", "ACTIVE")).upper() == "ACTIVE"
        ]

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        c = snapshot.payload
        uid = str(c["uid"])
        uid_fmt = (
            f"CHE-{uid[3:6]}.{uid[6:9]}.{uid[9:12]}" if uid.startswith("CHE") and len(uid) == 12 else uid
        )
        return [
            {
                "source_key": f"CH:{uid}",
                "legal_name": c.get("name"),
                "country": "CH",
                "city": c.get("legalSeat"),
                "registry_id": uid_fmt,
                "source_url": snapshot.source_url,
                "observed_at": _now_iso(),
            }
        ]


class CvrConnector(_Registry):
    """Denmark. Requires CVR_USERNAME/CVR_PASSWORD (Erhvervsstyrelsen system-to-system access)."""

    parser_version = f"cvr-{PARSER_VERSION}"
    api = "http://distribution.virk.dk/cvr-permanent/virksomhed/_search"
    public_url = "https://datacvr.virk.dk/enhed/virksomhed/{cvr}"

    def __init__(
        self, fixture_path: Path | None, live: bool, rate_limit_per_minute: int | None, auth: tuple[str, str]
    ):
        super().__init__(fixture_path, live, rate_limit_per_minute)
        self.auth = auth

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        if self.live and not all(self.auth):
            raise ConnectorError("CVR credentials missing (CVR_USERNAME/CVR_PASSWORD); failing closed")
        limit = int(query.get("max_records", 100))
        must: list[dict[str, Any]] = [
            {
                "range": {
                    "Vrvirksomhed.virksomhedMetadata.nyesteAarsbeskaeftigelse.antalAnsatte": {
                        "gte": min_employees
                    }
                }
            },
            {"term": {"Vrvirksomhed.virksomhedMetadata.sammensatStatus": "Aktiv"}},
        ]
        if query.get("industry_code"):
            must.append(
                {
                    "prefix": {
                        "Vrvirksomhed.virksomhedMetadata.nyesteHovedbranche.branchekode": str(
                            query["industry_code"]
                        )
                    }
                }
            )
        body = {"size": limit, "query": {"bool": {"must": must}}}
        if self.live:
            resp = self.client.request("POST", self.api, json=body, auth=self.auth)
            if resp.status >= 400:
                raise ConnectorError(f"CVR search returned HTTP {resp.status}")
            hits = json.loads(resp.text).get("hits", {}).get("hits", [])
        else:
            hits = self._fixture().get("hits", {}).get("hits", [])
        refs = []
        for h in hits:
            v = h.get("_source", {}).get("Vrvirksomhed", {})
            cvr = str(v.get("cvrNummer", ""))
            if cvr:
                refs.append(
                    CandidateRef(
                        cvr, self.public_url.format(cvr=cvr), {"record": v, "request": "cvr:_search"}
                    )
                )
        return refs

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        v = snapshot.payload
        meta = v.get("virksomhedMetadata", {})
        addr = meta.get("nyesteBeliggenhedsadresse") or {}
        branche = meta.get("nyesteHovedbranche") or {}
        emp = (meta.get("nyesteAarsbeskaeftigelse") or {}).get("antalAnsatte")
        homepages = [
            h.get("kontaktoplysning")
            for h in v.get("hjemmeside", [])
            if not (h.get("periode") or {}).get("gyldigTil")
        ]
        return [
            {
                "source_key": f"DK:{v['cvrNummer']}",
                "legal_name": (meta.get("nyesteNavn") or {}).get("navn"),
                "country": "DK",
                "city": addr.get("postdistrikt"),
                "registry_id": str(v["cvrNummer"]),
                "vat_id": f"DK{v['cvrNummer']}",
                "industry_code": branche.get("branchekode"),
                "sector": branche.get("branchetekst"),
                "employees": str(emp) if emp is not None else None,
                "website": homepages[0] if homepages else None,
                "source_url": snapshot.source_url,
                "observed_at": _now_iso(),
            }
        ]
