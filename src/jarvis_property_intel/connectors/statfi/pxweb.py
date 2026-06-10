"""Tilastokeskus (Statistics Finland) PxWeb API connector.

Fetches housing-price statistics and transaction volumes from the
Statistics Finland PxWeb service.  The PxWeb API uses POST requests
with a JSON query body that specifies dimension selections and the
desired response format (JSON-stat2).

Supported tables (pre-configured in :class:`StatFiConfig`):

* ``statfin_ashi_pxt_112p.px`` — Old apartment prices & volumes by postal
  code area.
* ``statfin_ashi_pxt_112q.px`` — Old apartment price index (quarterly).
* ``statfin_ashi_pxt_112r.px`` — Monthly price indices.

Usage::

    from jarvis_property_intel.connectors.statfi.config import StatFiConfig
    from jarvis_property_intel.connectors.statfi.pxweb import StatFiPxWebConnector

    connector = StatFiPxWebConnector(StatFiConfig())
    results = await connector.fetch_dataset("apartment_prices")
    for raw in results:
        records = connector.normalize(raw)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from ..base import NormalizedRecord, RawFetchResult
from .config import StatFiConfig

logger = logging.getLogger(__name__)

# Friendly aliases → actual table paths resolved at runtime via config.
_TABLE_ALIASES: dict[str, str] = {
    "apartment_prices": "apartment_prices_table",
    "price_index": "price_index_table",
    "monthly_index": "monthly_index_table",
}


class StatFiPxWebConnector:
    """Connector for Tilastokeskus PxWeb statistical tables.

    Implements the :class:`~packages.connectors.base.StatisticsSourceConnector`
    protocol.
    """

    source_id: str = "statfi_pxweb"

    def __init__(self, config: StatFiConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = asyncio.Semaphore(config.max_concurrent)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
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
        """Return ``True`` if the PxWeb API responds to a metadata request."""
        try:
            client = await self._get_client()
            # A lightweight GET to the apartment_prices table metadata
            url = f"{self._config.base_url}/{self._config.apartment_prices_table}"
            resp = await client.get(url)
            return resp.status_code < 400
        except Exception:
            logger.exception("StatFi PxWeb health-check failed")
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Generic fetch — delegates to :meth:`fetch_dataset`."""
        dataset_id = kwargs.pop("dataset_id", "apartment_prices")
        query = kwargs.pop("query", None)
        return await self.fetch_dataset(dataset_id=dataset_id, query=query, **kwargs)

    async def fetch_dataset(
        self,
        dataset_id: str = "apartment_prices",
        query: dict[str, Any] | None = None,
        **params: Any,
    ) -> list[RawFetchResult]:
        """POST a query to a PxWeb table and return the JSON-stat2 response.

        Args:
            dataset_id: Either a friendly alias (``"apartment_prices"``,
                ``"price_index"``, ``"monthly_index"``) or a raw PxWeb
                table path (e.g.
                ``"StatFin/asu/ashi/nj/statfin_ashi_pxt_112p.px"``).
            query: PxWeb JSON query body.  If ``None`` a sensible default
                query is constructed that requests all available data in
                JSON-stat2 format.
            **params: Additional keyword arguments merged into the query
                body (e.g. ``postal_codes``, ``years``).

        Returns:
            A single-element list containing the :class:`RawFetchResult`.
        """
        table_path = self._resolve_table_path(dataset_id)
        url = f"{self._config.base_url}/{table_path}"

        if query is None:
            query = self._build_default_query(dataset_id, **params)

        client = await self._get_client()
        async with self._rate_limiter:
            logger.debug("StatFi PxWeb POST %s", url)
            resp = await client.post(url, json=query)
            resp.raise_for_status()

        fetched_at = datetime.now(tz=UTC)
        return [
            RawFetchResult(
                source_id=self.source_id,
                fetched_at=fetched_at,
                raw_content=resp.content,
                content_type=resp.headers.get("content-type", "application/json"),
                parse_version="jsonstat2_v1",
                url=str(resp.url),
            ),
        ]

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a PxWeb JSON-stat2 response into normalized records."""
        return self.normalize_statistics(raw)

    def normalize_statistics(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a JSON-stat2 response into area-snapshot records.

        JSON-stat2 structure::

            {
                "id": ["Alue", "Vuosineljannes", "Tiedot"],
                "size": [N, M, K],
                "dimension": {
                    "Alue": {"category": {"index": {...}, "label": {...}}},
                    ...
                },
                "value": [...]
            }

        Each combination of dimension values is expanded into a separate
        :class:`NormalizedRecord`.
        """
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode StatFi response from %s", raw.url)
            return []

        records: list[NormalizedRecord] = []

        # JSON-stat2 top-level keys
        dimension_ids: list[str] = data.get("id", [])
        sizes: list[int] = data.get("size", [])
        dimensions: dict[str, Any] = data.get("dimension", {})
        values: list[Any] = data.get("value", [])

        if not dimension_ids or not values:
            # Possibly a different format — try flat table fallback
            return self._normalize_flat_table(data, raw.fetched_at)

        # Build ordered category labels for each dimension
        dim_labels: list[list[str]] = []
        dim_keys: list[list[str]] = []
        for dim_id in dimension_ids:
            dim = dimensions.get(dim_id, {})
            category = dim.get("category", {})
            index_map: dict[str, int] = category.get("index", {})
            label_map: dict[str, str] = category.get("label", {})
            # Sort by index position
            sorted_keys = sorted(index_map, key=lambda k: index_map[k])
            dim_keys.append(sorted_keys)
            dim_labels.append([label_map.get(k, k) for k in sorted_keys])

        # Iterate over the flat value array using mixed-radix indexing
        total = len(values)
        for flat_idx in range(total):
            if values[flat_idx] is None:
                continue

            # Decompose flat index into per-dimension indices
            remainder = flat_idx
            indices: list[int] = []
            for s in reversed(sizes):
                indices.append(remainder % s)
                remainder //= s
            indices.reverse()

            # Build a record keyed by dimension name
            record_data: dict[str, Any] = {"value": values[flat_idx]}
            composite_key_parts: list[str] = []
            for i, dim_id in enumerate(dimension_ids):
                idx = indices[i]
                if idx < len(dim_keys[i]):
                    key = dim_keys[i][idx]
                    label = dim_labels[i][idx]
                    record_data[dim_id] = key
                    record_data[f"{dim_id}_label"] = label
                    composite_key_parts.append(key)

            source_record_id = "|".join(composite_key_parts) if composite_key_parts else str(uuid.uuid4())

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="area_stats",
                    source_record_id=source_record_id,
                    data=record_data,
                    fetched_at=raw.fetched_at,
                ),
            )

        logger.info("StatFi: normalized %d records from %s", len(records), raw.url)
        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_table_path(self, dataset_id: str) -> str:
        """Resolve a friendly alias or return the path as-is."""
        attr_name = _TABLE_ALIASES.get(dataset_id)
        if attr_name is not None:
            return getattr(self._config, attr_name)
        # Assume it is a raw table path
        return dataset_id

    def _build_default_query(self, dataset_id: str, **params: Any) -> dict[str, Any]:
        """Construct a default PxWeb query body.

        PxWeb expects a ``query`` list of dimension selections and a
        ``response`` block specifying the output format.

        If no specific selections are given the query requests all
        available values for each dimension (``"selection": {"filter":
        "all", "values": ["*"]}``).
        """
        query_items: list[dict[str, Any]] = []

        # Allow callers to pass dimension filters via params
        postal_codes: list[str] | None = params.get("postal_codes")
        quarters: list[str] | None = params.get("quarters")
        years: list[str] | None = params.get("years")
        building_types: list[str] | None = params.get("building_types")

        # PxWeb-päivitys 2026-06-08: dimensiokoodit ovat teknisiä id:itä
        # (ashi-taulujen 13mt/13mp/13ms koodit alla); vanhat suomenkieliset
        # nimet eivät enää kelpaa aktiivisessa StatFin-kannassa.
        if postal_codes:
            query_items.append({
                "code": "postinumeroalue_4_20220101",
                "selection": {
                    "filter": "item",
                    "values": postal_codes,
                },
            })
        if quarters:
            query_items.append({
                "code": "timeperiod_q",
                "selection": {
                    "filter": "item",
                    "values": quarters,
                },
            })
        if years:
            query_items.append({
                "code": "timeperiod_y",
                "selection": {
                    "filter": "item",
                    "values": years,
                },
            })
        if building_types:
            query_items.append({
                "code": "talotyyppi_6_20131021",
                "selection": {
                    "filter": "item",
                    "values": building_types,
                },
            })

        # If no specific filters, request a small default slice
        if not query_items:
            query_items.append({
                "code": "timeperiod_q",
                "selection": {
                    "filter": "top",
                    "values": ["4"],
                },
            })

        return {
            "query": query_items,
            "response": {
                "format": "json-stat2",
            },
        }

    def _normalize_flat_table(
        self,
        data: dict[str, Any],
        fetched_at: datetime,
    ) -> list[NormalizedRecord]:
        """Fallback normalizer for non-JSON-stat2 responses.

        Some PxWeb responses may come back as a simple ``{"columns": [...],
        "data": [...]}`` table.
        """
        columns: list[dict[str, str]] = data.get("columns", [])
        rows: list[dict[str, Any]] = data.get("data", [])
        if not columns or not rows:
            logger.warning("StatFi: unrecognised response structure")
            return []

        col_codes = [c.get("code", c.get("text", f"col_{i}")) for i, c in enumerate(columns)]
        records: list[NormalizedRecord] = []

        for row_idx, row in enumerate(rows):
            key_values = row.get("key", [])
            data_values = row.get("values", [])
            record_data: dict[str, Any] = {}

            for i, val in enumerate(key_values):
                if i < len(col_codes):
                    record_data[col_codes[i]] = val
            for i, val in enumerate(data_values):
                col_offset = len(key_values) + i
                if col_offset < len(col_codes):
                    record_data[col_codes[col_offset]] = val

            source_record_id = "|".join(str(v) for v in key_values) if key_values else str(row_idx)

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="area_stats",
                    source_record_id=source_record_id,
                    data=record_data,
                    fetched_at=fetched_at,
                ),
            )

        return records
