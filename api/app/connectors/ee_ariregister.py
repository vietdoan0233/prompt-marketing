"""Estonian e-Business Register open data (https://avaandmed.ariregister.rik.ee/) — official bulk files only.

No company pages or annual-report PDFs are scraped. The connector:
1. reads the portal's download page to find the *current* official file names (they embed a monthly cut-off
   date, e.g. `4.2024_aruannete_elemendid_kuni_31082026.zip`);
2. downloads a file only when the portal copy changed (Last-Modified / size), keeping a manifest with the
   SHA-256 of every file so each imported fact can name the exact dataset file it came from;
3. streams the semicolon-separated CSVs inside the zips.

Datasets used:
- basic data   `ettevotja_rekvisiidid__lihtandmed.csv.zip` — name, registry code, legal form, VAT, status,
                                                            registered address
- reports      `1.aruannete_yldandmed_kuni_*.zip`          — annual report ID and period
- activity     `2.EMTAK_myygitulu_kuni_*.zip`              — revenue split by EMTAK; main activity flag
- indicators   `4.<year>_aruannete_elemendid_kuni_*.zip`   — key indicators per report (long format)
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from app.connectors.base import ConnectorError
from app.connectors.http import PoliteClient

PORTAL = "https://avaandmed.ariregister.rik.ee"
DOWNLOAD_PAGE = f"{PORTAL}/et/avaandmete-allalaadimine"
EE_PARSER_VERSION = "ee-ariregister-2026.09.1"
MANIFEST = "manifest.json"
QUALIFICATION_YEARS = {2024, 2025}

_PATTERNS = {
    "basic": re.compile(r"/sites/default/files/avaandmed/ettevotja_rekvisiidid__lihtandmed\.csv\.zip"),
    "reports": re.compile(r"/sites/default/files/1\.aruannete_yldandmed_kuni_\d{8}\.zip"),
    "activity": re.compile(r"/sites/default/files/2\.EMTAK_myygitulu_kuni_\d{8}\.zip"),
    "indicators": re.compile(r"/sites/default/files/4\.(\d{4})_aruannete_elemendid_kuni_\d{8}\.zip"),
}


@dataclass
class DatasetFile:
    kind: str  # basic | reports | activity | indicators
    name: str
    url: str
    path: str
    sha256: str
    size: int
    last_modified: str  # ISO 8601 UTC, from the portal's Last-Modified header (dataset publication time)
    year: int | None = None

    @property
    def published_at(self) -> datetime:
        return datetime.fromisoformat(self.last_modified)

    def meta(self) -> dict:
        d = asdict(self)
        d.pop("path")
        return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_links(html: str) -> dict[str, list[tuple[str, int | None]]]:
    found: dict[str, list[tuple[str, int | None]]] = {k: [] for k in _PATTERNS}
    for href in sorted(set(re.findall(r'href="([^"]+)"', html))):
        path = href.replace(PORTAL, "")
        for kind, pattern in _PATTERNS.items():
            m = pattern.fullmatch(path)
            if m:
                year = int(m.group(1)) if kind == "indicators" else None
                found[kind].append((f"{PORTAL}{path}", year))
    return found


class EeAriregisterFiles:
    """Resolves the dataset files: live from the portal, or from a local directory with a manifest (tests)."""

    def __init__(self, cache_dir: Path, live: bool, rate_limit_per_minute: int | None = 30) -> None:
        self.cache_dir = cache_dir
        self.live = live
        self.rate = rate_limit_per_minute
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _manifest(self) -> dict[str, dict]:
        p = self.cache_dir / MANIFEST
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _save_manifest(self, manifest: dict[str, dict]) -> None:
        (self.cache_dir / MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

    def resolve(self, years: list[int]) -> list[DatasetFile]:
        required_years = sorted(set(years) | QUALIFICATION_YEARS)
        return self._resolve_live(required_years) if self.live else self._resolve_local(required_years)

    def _resolve_local(self, years: list[int]) -> list[DatasetFile]:
        manifest = self._manifest()
        if not manifest:
            raise ConnectorError(f"no manifest in {self.cache_dir}; live download is disabled")
        files = []
        for name, m in manifest.items():
            if m["kind"] == "indicators" and m.get("year") not in years:
                continue
            path = self.cache_dir / name
            if path.name != name or not m["url"].startswith(f"{PORTAL}/sites/default/files/"):
                raise ConnectorError(f"{name}: cache entry is not an official portal file")
            if not path.is_file() or path.stat().st_size != m["size"] or _sha256(path) != m["sha256"]:
                raise ConnectorError(f"{name}: cached file is missing or does not match its recorded hash")
            files.append(DatasetFile(path=str(path), **m))
        self._check_complete(files, years)
        return files

    def _resolve_live(self, years: list[int]) -> list[DatasetFile]:
        client = PoliteClient(self.rate)
        try:
            page = client.request("GET", DOWNLOAD_PAGE)
            if page.status >= 400:
                raise ConnectorError(f"download page returned HTTP {page.status}")
            links = discover_links(page.text)
            manifest = self._manifest()
            wanted = [
                (k, url, y) for k, lst in links.items() for url, y in lst if k != "indicators" or y in years
            ]
            files = []
            for kind, url, year in wanted:
                files.append(self._fetch(client, manifest, kind, url, year))
            self._save_manifest(manifest)
        finally:
            client.close()
        self._check_complete(files, years)
        return files

    def _fetch(
        self, client: PoliteClient, manifest: dict, kind: str, url: str, year: int | None
    ) -> DatasetFile:
        name = url.rsplit("/", 1)[1]
        path = self.cache_dir / name
        head = client._client.head(url)  # metadata only; polite client throttles the GETs below
        if head.status_code >= 400:
            raise ConnectorError(f"HEAD {url} returned HTTP {head.status_code}")
        size = int(head.headers.get("content-length", "0"))
        lm_header = head.headers.get("last-modified")
        last_modified = (
            parsedate_to_datetime(lm_header).astimezone(UTC).isoformat()
            if lm_header
            else datetime.now(UTC).isoformat()
        )
        cached = manifest.get(name)
        if cached is None and path.exists() and path.stat().st_size == size:
            # A previous interrupted run may have cached a file with the same name and size.
            cached = {
                "kind": kind,
                "name": name,
                "url": url,
                "sha256": _sha256(path),
                "size": size,
                "last_modified": last_modified,
                "year": year,
            }
            manifest[name] = cached
        if not (
            cached and path.exists() and cached["size"] == size and cached["last_modified"] == last_modified
        ):
            tmp = path.with_suffix(".part")
            with client._client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ConnectorError(f"GET {url} returned HTTP {resp.status_code}")
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
            tmp.replace(path)
            cached = {
                "kind": kind,
                "name": name,
                "url": url,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "last_modified": last_modified,
                "year": year,
            }
            manifest[name] = cached
        return DatasetFile(path=str(path), **cached)

    @staticmethod
    def _check_complete(files: list[DatasetFile], years: list[int]) -> None:
        kinds = {f.kind for f in files}
        missing = {"basic", "reports", "activity"} - kinds
        if missing:
            raise ConnectorError(f"official dataset files not found on the portal: {sorted(missing)}")
        have_years = {f.year for f in files if f.kind == "indicators"}
        missing_years = sorted(set(years) - have_years)
        if missing_years:
            raise ConnectorError(f"missing key-indicator files for fiscal years {missing_years}")


def iter_rows(file: DatasetFile) -> Iterator[dict[str, str]]:
    """Stream rows of the single CSV inside an official zip (UTF-8 with BOM, ';'-separated)."""
    try:
        zf = zipfile.ZipFile(file.path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ConnectorError(f"{file.name} is not a readable zip: {exc}") from exc
    with zf:
        members = [i for i in zf.infolist() if i.filename.lower().endswith(".csv")]
        if len(members) != 1:
            raise ConnectorError(f"{file.name}: expected exactly one CSV, found {len(members)}")
        with zf.open(members[0]) as fh:
            yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline=""), delimiter=";")
