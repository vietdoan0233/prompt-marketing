"""Connector interface. Provider-specific fetching/parsing lives behind this boundary.

    discover(query, region, min_employees) -> candidate references
    fetch(reference)                       -> FetchedSnapshot (raw provider payload)
    parse(snapshot)                        -> canonical rows (dicts keyed by records.CANONICAL_FIELDS)

Validation and upsert are shared pipeline stages (app.services.ingestion), so every connector gets
the same permission gate, allowed-field enforcement, provenance and idempotency rules.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


class ConnectorError(Exception):
    pass


@dataclass
class CandidateRef:
    reference: str  # provider-stable key, e.g. an organisation number
    source_url: str | None = None
    hint: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchedSnapshot:
    reference: str
    payload: Any
    source_url: str | None
    http_status: int | None = None
    request_id: str | None = None


class Connector(Protocol):
    parser_version: str

    def discover(
        self, query: dict[str, Any], region: str | None, min_employees: int
    ) -> list[CandidateRef]: ...

    def fetch(self, ref: CandidateRef) -> FetchedSnapshot: ...

    def parse(self, snapshot: FetchedSnapshot) -> list[dict[str, Any]]: ...
