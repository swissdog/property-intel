"""MML kauppahintarekisteri (property transaction register) connector.

Fetches property transaction data from Maanmittauslaitos open-data APIs.
Supports two backends via the adapter pattern:

* **OGC API Features** (default, spring 2026+) — GeoJSON FeatureCollection
  at ``/ogcapi/kiinteistokaupat/v1/collections/kiinteistokaupat/items``.
* **Legacy REST** — older JSON endpoint at
  ``/kiinteistokaupat/v1``.

The active backend is selected by :pyattr:`MMLConfig.api_version`.

Usage::

    from jarvis_property_intel.connectors.mml.config import MMLConfig
    from jarvis_property_intel.connectors.mml.transactions import MMLTransactionConnector

    connector = MMLTransactionConnector(MMLConfig())
    results = await connector.fetch_transactions(municipality="091")
    for raw in results:
        records = connector.normalize(raw)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ..base import NormalizedRecord, RawFetchResult
from .config import MMLConfig

logger = logging.getLogger(__name__)


class MMLTransactionConnector:
    """Connector for MML kiinteistökaupat (property transaction) data.

    Implements the :class:`~packages.connectors.base.TransactionSourceConnector`
    protocol.
    """

    source_id: str = "mml_transactions"

    def __init__(self, config: MMLConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = asyncio.Semaphore(config.max_concurrent)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) a shared :class:`httpx.AsyncClient`."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/geo+json, application/json"}
            if self._config.api_key:
                headers["X-API-Key"] = self._config.api_key
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return ``True`` if the MML API is reachable."""
        try:
            client = await self._get_client()
            if self._config.api_version == "ogc":
                url = (
                    f"{self._config.base_url}"
                    "/ogcapi/kiinteistokaupat/v1/collections/kiinteistokaupat"
                )
            else:
                url = f"{self._config.base_url}/kiinteistokaupat/v1"

            params: dict[str, str] = {}
            if self._config.api_key:
                params["api_key"] = self._config.api_key

            resp = await client.get(url, params=params)
            return resp.status_code < 400
        except Exception:
            logger.exception("MML health-check failed")
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Generic fetch — delegates to :meth:`fetch_transactions`."""
        return await self.fetch_transactions(**kwargs)

    async def fetch_transactions(
        self,
        municipality: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[RawFetchResult]:
        """Fetch transaction records, routed to the configured API backend.

        Args:
            municipality: Finnish municipality code (e.g. ``"091"`` for Helsinki).
            date_from: Start of the date range (inclusive).
            date_to: End of the date range (inclusive).
            bbox: Bounding box as ``(min_lon, min_lat, max_lon, max_lat)``.

        Returns:
            A list of :class:`RawFetchResult` objects — one per API response page.
        """
        if self._config.api_version == "ogc":
            return await self._fetch_ogc(
                municipality=municipality,
                date_from=date_from,
                date_to=date_to,
                bbox=bbox,
            )
        return await self._fetch_legacy(
            municipality=municipality,
            date_from=date_from,
            date_to=date_to,
            bbox=bbox,
        )

    # ------------------------------------------------------------------
    # OGC API Features backend
    # ------------------------------------------------------------------

    async def _fetch_ogc(
        self,
        municipality: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[RawFetchResult]:
        """Paginate through the OGC API Features endpoint."""
        client = await self._get_client()
        base = (
            f"{self._config.base_url}"
            "/ogcapi/kiinteistokaupat/v1/collections/kiinteistokaupat/items"
        )

        params: dict[str, str] = {
            "limit": str(self._config.page_size),
        }
        if self._config.api_key:
            params["api_key"] = self._config.api_key
        if bbox is not None:
            params["bbox"] = ",".join(str(c) for c in bbox)
        if date_from or date_to:
            dt_start = date_from.isoformat() if date_from else ".."
            dt_end = date_to.isoformat() if date_to else ".."
            params["datetime"] = f"{dt_start}/{dt_end}"
        if municipality:
            params["kuntanumero"] = municipality

        results: list[RawFetchResult] = []
        offset = 0

        while True:
            params["offset"] = str(offset)
            async with self._rate_limiter:
                logger.debug("MML OGC fetch offset=%d params=%s", offset, params)
                resp = await client.get(base, params=params)
                resp.raise_for_status()

            fetched_at = datetime.now(tz=UTC)
            body = resp.content
            results.append(
                RawFetchResult(
                    source_id=self.source_id,
                    fetched_at=fetched_at,
                    raw_content=body,
                    content_type=resp.headers.get("content-type", "application/geo+json"),
                    parse_version="ogc_v1",
                    url=str(resp.url),
                ),
            )

            # Check if there are more pages
            data = resp.json()
            returned = len(data.get("features", []))
            if returned < self._config.page_size:
                break
            offset += returned

        logger.info(
            "MML OGC: fetched %d page(s), municipality=%s",
            len(results),
            municipality,
        )
        return results

    # ------------------------------------------------------------------
    # Legacy REST backend
    # ------------------------------------------------------------------

    async def _fetch_legacy(
        self,
        municipality: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[RawFetchResult]:
        """Paginate through the legacy REST endpoint."""
        client = await self._get_client()
        base = f"{self._config.base_url}/kiinteistokaupat/v1"

        params: dict[str, str] = {
            "limit": str(self._config.page_size),
        }
        if self._config.api_key:
            params["api_key"] = self._config.api_key
        if bbox is not None:
            params["bbox"] = ",".join(str(c) for c in bbox)
        if date_from:
            params["date_from"] = date_from.isoformat()
        if date_to:
            params["date_to"] = date_to.isoformat()
        if municipality:
            params["municipality"] = municipality

        results: list[RawFetchResult] = []
        page = 1

        while True:
            params["page"] = str(page)
            async with self._rate_limiter:
                logger.debug("MML legacy fetch page=%d params=%s", page, params)
                resp = await client.get(base, params=params)
                resp.raise_for_status()

            fetched_at = datetime.now(tz=UTC)
            body = resp.content
            results.append(
                RawFetchResult(
                    source_id=self.source_id,
                    fetched_at=fetched_at,
                    raw_content=body,
                    content_type=resp.headers.get("content-type", "application/json"),
                    parse_version="legacy_v1",
                    url=str(resp.url),
                ),
            )

            data = resp.json()
            # Legacy API: check for next page indicator
            returned = len(data.get("results", data.get("features", [])))
            if returned < self._config.page_size:
                break
            page += 1

        logger.info(
            "MML legacy: fetched %d page(s), municipality=%s",
            len(results),
            municipality,
        )
        return results

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a raw MML response into :class:`NormalizedRecord` objects.

        Handles both OGC (GeoJSON FeatureCollection) and legacy JSON
        response formats.
        """
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode MML response from %s", raw.url)
            return []

        records: list[NormalizedRecord] = []

        if raw.parse_version == "ogc_v1":
            records = self._normalize_ogc(data, raw.fetched_at)
        elif raw.parse_version == "legacy_v1":
            records = self._normalize_legacy(data, raw.fetched_at)
        else:
            logger.warning("Unknown parse_version %r", raw.parse_version)

        return records

    def normalize_transaction(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Alias for :meth:`normalize` (protocol compatibility)."""
        return self.normalize(raw)

    # ------------------------------------------------------------------
    # Internal normalizers
    # ------------------------------------------------------------------

    def _normalize_ogc(
        self,
        data: dict[str, Any],
        fetched_at: datetime,
    ) -> list[NormalizedRecord]:
        """Parse an OGC API Features GeoJSON FeatureCollection."""
        records: list[NormalizedRecord] = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            geometry = feature.get("geometry")
            source_record_id = props.get("tunniste") or props.get("id") or str(uuid.uuid4())

            record_data: dict[str, Any] = {
                "transaction_date": props.get("kauppapvm") or props.get("luovutuspvm"),
                "transaction_price": _safe_float(props.get("kauppahinta")),
                "transaction_type": props.get("luovutustyyppi", "sale"),
                "municipality_code": props.get("kuntanumero"),
                "municipality_name": props.get("kuntanimi"),
                "parcel_id": props.get("kiinteistotunnus"),
                "area_m2": _safe_float(props.get("pinta_ala")),
                "unit_price_m2": _safe_float(props.get("yksikkohinta")),
                "property_type": props.get("kauppatyyppi") or props.get("kohdetyyppi"),
            }

            if geometry:
                record_data["geometry"] = geometry
                coords = geometry.get("coordinates")
                if coords and geometry.get("type") == "Point":
                    record_data["lon"] = coords[0]
                    record_data["lat"] = coords[1]

            # Strip None values for cleanliness
            record_data = {k: v for k, v in record_data.items() if v is not None}

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="transaction",
                    source_record_id=str(source_record_id),
                    data=record_data,
                    fetched_at=fetched_at,
                ),
            )
        return records

    def _normalize_legacy(
        self,
        data: dict[str, Any],
        fetched_at: datetime,
    ) -> list[NormalizedRecord]:
        """Parse a legacy REST API JSON response."""
        records: list[NormalizedRecord] = []
        items = data.get("results", data.get("features", []))
        for item in items:
            # Legacy responses may be flat dicts or have a properties sub-dict
            props = item.get("properties", item)
            source_record_id = (
                props.get("tunniste")
                or props.get("id")
                or item.get("id")
                or str(uuid.uuid4())
            )

            record_data: dict[str, Any] = {
                "transaction_date": props.get("kauppapvm") or props.get("luovutuspvm"),
                "transaction_price": _safe_float(props.get("kauppahinta")),
                "transaction_type": props.get("luovutustyyppi", "sale"),
                "municipality_code": props.get("kuntanumero"),
                "municipality_name": props.get("kuntanimi"),
                "parcel_id": props.get("kiinteistotunnus"),
                "area_m2": _safe_float(props.get("pinta_ala")),
                "unit_price_m2": _safe_float(props.get("yksikkohinta")),
                "property_type": props.get("kauppatyyppi") or props.get("kohdetyyppi"),
            }

            geometry = item.get("geometry")
            if geometry:
                record_data["geometry"] = geometry
                coords = geometry.get("coordinates")
                if coords and geometry.get("type") == "Point":
                    record_data["lon"] = coords[0]
                    record_data["lat"] = coords[1]

            record_data = {k: v for k, v in record_data.items() if v is not None}

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="transaction",
                    source_record_id=str(source_record_id),
                    data=record_data,
                    fetched_at=fetched_at,
                ),
            )
        return records


def _safe_float(value: Any) -> float | None:
    """Attempt to convert *value* to float; return ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
