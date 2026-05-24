"""SYKE flood-risk connector — ArcGIS REST query API.

Fetches Finland's flood-hazard polygons from SYKE's open ArcGIS REST
endpoints under /arcgis/rest/services/Tulva/. Each scenario
(100y / 250y / significant) is a (MapServer service, layerId) pair.

Pagination uses ``resultOffset`` + ``resultRecordCount`` (the standard
ArcGIS REST pattern). Output format is GeoJSON, returned in EPSG:4326
so PostGIS can store it without reprojection.

Usage::

    from jarvis_property_intel.connectors.syke_flood import SykeFloodConfig, SykeFloodConnector

    connector = SykeFloodConnector(SykeFloodConfig())
    raws = await connector.fetch_dataset()
    for raw in raws:
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
from .config import SykeFloodConfig, SykeFloodLayer

logger = logging.getLogger(__name__)


class SykeFloodConnector:
    """Connector for SYKE ArcGIS REST flood-risk layers.

    Implements the :class:`~packages.connectors.base.StatisticsSourceConnector`
    protocol (each scenario polygon is treated as an "area_stats" record).
    """

    source_id: str = "syke_flood"

    def __init__(self, config: SykeFloodConfig) -> None:
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
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _layer_url(self, layer: SykeFloodLayer) -> str:
        return (
            f"{self._config.rest_base_url.rstrip('/')}/"
            f"{layer.service}/MapServer/{layer.layer_id}"
        )

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return ``True`` if the SYKE REST root + first layer respond."""
        try:
            client = await self._get_client()
            url = self._layer_url(self._config.layers[0])
            resp = await client.get(url, params={"f": "json"})
            if resp.status_code >= 400:
                return False
            data = resp.json()
            # ArcGIS errors come back with HTTP 200 + {"error": {...}}
            return "error" not in data
        except Exception:
            logger.exception("SYKE flood REST health-check failed")
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Generic fetch — delegates to :meth:`fetch_dataset`."""
        return await self.fetch_dataset(**kwargs)

    async def fetch_dataset(
        self,
        dataset_id: str | None = None,
        **params: Any,
    ) -> list[RawFetchResult]:
        """Issue a paginated REST /query request for each configured layer.

        Args:
            dataset_id: Optional scenario filter (``"100y"`` / ``"250y"`` /
                ``"significant"``). When ``None`` all configured layers are
                fetched.
            **params: Reserved for future use.

        Returns:
            A list of :class:`RawFetchResult` — one per page across all
            requested layers.
        """
        layers: list[SykeFloodLayer] = list(self._config.layers)
        if dataset_id:
            layers = [ly for ly in layers if ly.scenario == dataset_id]
            if not layers:
                logger.warning(
                    "No SYKE layer matches scenario=%s — known: %s",
                    dataset_id,
                    [ly.scenario for ly in self._config.layers],
                )
                return []

        client = await self._get_client()
        all_results: list[RawFetchResult] = []

        for layer in layers:
            url = f"{self._layer_url(layer)}/query"
            offset = 0
            while True:
                query_params: dict[str, str] = {
                    "where": "1=1",
                    "outFields": "*",
                    "outSR": str(self._config.out_sr),
                    "f": "geojson",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(self._config.max_features),
                    "returnGeometry": "true",
                }
                async with self._rate_limiter:
                    logger.debug(
                        "SYKE REST GET %s offset=%d count=%d",
                        url, offset, self._config.max_features,
                    )
                    resp = await client.get(url, params=query_params)
                    resp.raise_for_status()

                fetched_at = datetime.now(tz=UTC)
                body = resp.content
                all_results.append(
                    RawFetchResult(
                        source_id=self.source_id,
                        fetched_at=fetched_at,
                        raw_content=body,
                        content_type=resp.headers.get(
                            "content-type", "application/geo+json",
                        ),
                        parse_version=f"esri_geojson_v1:{layer.scenario}",
                        url=str(resp.url),
                        source_record_id=layer.scenario,
                    ),
                )

                # Pagination: ArcGIS sets exceededTransferLimit=true while
                # more records remain. Decode small enough to inspect.
                try:
                    data = resp.json()
                except Exception:
                    break

                returned = len(data.get("features", []))
                exceeded = data.get("exceededTransferLimit") or data.get(
                    "properties", {},
                ).get("exceededTransferLimit")

                if returned == 0:
                    break
                offset += returned
                if not exceeded:
                    break

        logger.info(
            "SYKE flood: fetched %d page(s) across %d layer(s)",
            len(all_results), len(layers),
        )
        return all_results

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a SYKE REST GeoJSON response into normalized records."""
        return self.normalize_statistics(raw)

    def normalize_statistics(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse GeoJSON features into per-polygon records.

        The scenario tag travels in ``parse_version``
        (``esri_geojson_v1:<scenario>``) and ``source_record_id`` set by
        :meth:`fetch_dataset`.
        """
        scenario = raw.source_record_id or self._scenario_from_parse_version(
            raw.parse_version,
        )

        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode SYKE response from %s", raw.url)
            return []

        records: list[NormalizedRecord] = []
        for feature in data.get("features", []):
            props: dict[str, Any] = feature.get("properties", {}) or {}
            geometry = feature.get("geometry")
            feature_id = (
                feature.get("id")
                or props.get("OBJECTID")
                or props.get("inspireId")
                or str(uuid.uuid4())
            )

            record_data: dict[str, Any] = {
                "scenario": scenario,
                "properties": props,
            }
            if geometry:
                record_data["geometry"] = geometry

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="area_stats",
                    source_record_id=f"{scenario}:{feature_id}",
                    data=record_data,
                    fetched_at=raw.fetched_at,
                ),
            )

        logger.info(
            "SYKE flood (%s): normalized %d records from %s",
            scenario, len(records), raw.url,
        )
        return records

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scenario_from_parse_version(parse_version: str) -> str:
        """Extract the scenario tag from a ``<format>:<scenario>`` tag."""
        if ":" in parse_version:
            return parse_version.rsplit(":", 1)[1]
        return "unknown"
