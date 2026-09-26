"""CSV connector for Mergero-supplied files and approved registry/licensed exports.

The source's `field_mapping` maps provider headers (e.g. Swedish 'Organisationsnummer', German
'Registernummer') onto canonical field names. Unmapped canonical headers pass through unchanged.
"""

import csv
import io
from typing import Any

from app.connectors.base import CandidateRef, ConnectorError, FetchedSnapshot
from app.domain.records import PARSER_VERSION


def decode_csv_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConnectorError("CSV is not UTF-8 or Windows-1252 encoded")


class CsvConnector:
    parser_version = f"csv-{PARSER_VERSION}"

    def __init__(self, field_mapping: dict[str, str] | None = None) -> None:
        self.field_mapping = {k.strip().lower(): v for k, v in (field_mapping or {}).items()}

    def discover(self, query: dict[str, Any], region: str | None, min_employees: int) -> list[CandidateRef]:
        raise ConnectorError("CSV sources are imported from a supplied file, not discovered")

    def fetch_text(self, file_name: str, text: str) -> FetchedSnapshot:
        return FetchedSnapshot(reference=file_name, payload=text, source_url=None)

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot:
        raise ConnectorError("CSV sources require an uploaded file")

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]:
        text: str = snapshot.payload
        if not text.strip():
            raise ConnectorError("CSV file is empty")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ConnectorError("CSV has no header row")
        rows: list[dict[str, Any]] = []
        for raw in reader:
            row: dict[str, Any] = {}
            for header, value in raw.items():
                if header is None:
                    continue
                key = self.field_mapping.get(header.strip().lower(), header.strip())
                row[key] = value
            rows.append(row)
        return rows
