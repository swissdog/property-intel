"""Protocol definitions for property-intel source connectors.

Each external data source (Oikotie, MML, StatFi, Paavo, energy certs, etc.)
implements one or more of the Protocol classes defined here.  The protocols are
intentionally thin so that adding a new source requires only a small adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Raw result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RawFetchResult:
    """Immutable container for a single fetch from an external source."""

    source_id: str
    """Identifier of the source connector that produced this result."""

    fetched_at: datetime
    """UTC timestamp of when the fetch occurred."""

    raw_content: bytes
    """The raw response body exactly as received."""

    content_type: str
    """MIME type of *raw_content*, e.g. ``"text/html"`` or ``"application/json"``."""

    parse_version: str
    """Version tag for the parser that should handle this content."""

    url: str | None = None
    """The URL that was fetched, if applicable."""

    source_record_id: str | None = None
    """Source-specific record identifier, if available at fetch time."""


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """A parsed, source-agnostic record ready for downstream processing."""

    source_id: str
    """Identifier of the source connector that produced this record."""

    record_type: str
    """Semantic type: ``"listing"``, ``"transaction"``, ``"area_stats"``, etc."""

    source_record_id: str
    """Source-specific unique identifier for this record."""

    data: dict[str, Any] = field(default_factory=dict)
    """Normalized key-value payload."""

    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    """UTC timestamp inherited from the originating fetch."""


# ---------------------------------------------------------------------------
# Protocol definitions
# ---------------------------------------------------------------------------

@runtime_checkable
class SourceConnector(Protocol):
    """Base protocol that every source connector must satisfy."""

    source_id: str
    """Unique identifier for this data source (e.g. ``"oikotie"``)."""

    async def health_check(self) -> bool:
        """Return ``True`` if the source is reachable and operational."""
        ...

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Perform a generic fetch against the source.

        Keyword arguments are source-specific.
        """
        ...

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a raw fetch result into zero or more normalized records."""
        ...


@runtime_checkable
class ListingSourceConnector(SourceConnector, Protocol):
    """A source that provides real-estate listings (e.g. Oikotie)."""

    async def fetch_search_page(self, region: str, page: int) -> RawFetchResult:
        """Fetch a single search-result page for *region*."""
        ...

    async def fetch_listing(self, source_listing_id: str) -> RawFetchResult:
        """Fetch the full detail page / payload for one listing."""
        ...

    def normalize_listing(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a listing fetch result into normalized records."""
        ...


@runtime_checkable
class TransactionSourceConnector(SourceConnector, Protocol):
    """A source that provides historical property transactions (e.g. MML)."""

    async def fetch_transactions(
        self,
        municipality: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[RawFetchResult]:
        """Fetch transaction records, optionally filtered by municipality and date range."""
        ...

    def normalize_transaction(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a transaction fetch result into normalized records."""
        ...


@runtime_checkable
class StatisticsSourceConnector(SourceConnector, Protocol):
    """A source that provides statistical datasets (e.g. StatFi, Paavo)."""

    async def fetch_dataset(
        self, dataset_id: str, **params: Any
    ) -> list[RawFetchResult]:
        """Fetch a statistical dataset identified by *dataset_id*."""
        ...

    def normalize_statistics(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a statistics fetch result into normalized records."""
        ...
