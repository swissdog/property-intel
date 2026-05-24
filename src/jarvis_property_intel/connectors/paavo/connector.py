"""Paavo postal-code area statistics connector.

Fetches demographic, income, education, and housing-stock data per
Finnish postal code from Tilastokeskus's Paavo WFS (OGC Web Feature
Service) endpoint.

The WFS service is hosted by Statistics Finland's GeoServer at
``https://geo.stat.fi/geoserver/postialue/wfs``.  Responses are
requested as GeoJSON (``outputFormat=application/json``).

Usage::

    from jarvis_property_intel.connectors.paavo.config import PaavoConfig
    from jarvis_property_intel.connectors.paavo.connector import PaavoConnector

    connector = PaavoConnector(PaavoConfig())
    results = await connector.fetch_dataset(postal_codes=["00100", "00200"])
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
from .config import PaavoConfig

logger = logging.getLogger(__name__)

# Paavo field prefixes and their semantic meaning.  Paavo layers expose
# dozens of columns; we normalise the most important ones into readable
# keys.  Field names follow the pattern ``<prefix>_<stat>`` where
# prefix encodes the data domain.
_FIELD_MAP: dict[str, str] = {
    # Population
    "he_vakiy": "population_total",
    "he_miehet": "population_male",
    "he_naiset": "population_female",
    "he_kika": "mean_age",
    # Income
    "hr_mtu": "median_income",
    "hr_ktu": "mean_income",
    # Education
    "ko_yl_kork": "education_higher",
    "ko_al_kork": "education_lower_higher",
    "ko_ammat": "education_vocational",
    "ko_perus": "education_basic",
    # Housing stock
    "ra_ke": "buildings_total",
    "ra_raky": "buildings_residential",
    "ra_muut": "buildings_other",
    "ra_asrak": "apartment_buildings",
    "ra_asunn": "dwellings_total",
    # Household
    "te_taly": "households_total",
    "te_as_valj": "households_rental",
    "te_omis_as": "households_owner_occupied",
    # Area and identifiers
    "posti_alue": "postal_code",
    "postinumeroalue": "postal_code",
    "nimi": "name",
    "kunta": "municipality_code",
    "kuntanimi": "municipality_name",
    "vuosi": "year",
}


class PaavoConnector:
    """Connector for Paavo postal-code area statistics (WFS).

    Implements the :class:`~packages.connectors.base.StatisticsSourceConnector`
    protocol.
    """

    source_id: str = "paavo"

    def __init__(self, config: PaavoConfig) -> None:
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

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return ``True`` if the Paavo WFS endpoint responds."""
        try:
            client = await self._get_client()
            resp = await client.get(
                self._config.wfs_url,
                params={
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetCapabilities",
                },
            )
            return resp.status_code < 400
        except Exception:
            logger.exception("Paavo WFS health-check failed")
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Generic fetch — delegates to :meth:`fetch_dataset`."""
        return await self.fetch_dataset(**kwargs)

    async def fetch_dataset(
        self,
        dataset_id: str = "pno_tilasto",
        postal_codes: list[str] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        **params: Any,
    ) -> list[RawFetchResult]:
        """Issue a WFS GetFeature request for Paavo postal-code statistics.

        Args:
            dataset_id: Ignored for Paavo (single layer); accepted for
                protocol compatibility.
            postal_codes: Optional list of 5-digit Finnish postal codes to
                filter on.  Generates a CQL filter.
            bbox: Optional bounding box ``(min_lon, min_lat, max_lon, max_lat)``.
            **params: Additional keyword arguments (reserved for future use).

        Returns:
            A list of :class:`RawFetchResult` objects.  Typically a single
            element unless the response is paginated.
        """
        client = await self._get_client()

        wfs_params: dict[str, str] = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": self._config.layer,
            "outputFormat": "application/json",
            "srsName": self._config.srs_name,
            "count": str(self._config.max_features),
        }

        # Build CQL filter for specific postal codes
        if postal_codes:
            escaped = ",".join(f"'{pc}'" for pc in postal_codes)
            wfs_params["CQL_FILTER"] = f"postinumeroalue IN ({escaped})"

        if bbox is not None:
            wfs_params["bbox"] = ",".join(str(c) for c in bbox)

        results: list[RawFetchResult] = []
        start_index = 0

        while True:
            wfs_params["startIndex"] = str(start_index)
            async with self._rate_limiter:
                logger.debug(
                    "Paavo WFS GetFeature startIndex=%d postal_codes=%s",
                    start_index,
                    postal_codes,
                )
                resp = await client.get(self._config.wfs_url, params=wfs_params)
                resp.raise_for_status()

            fetched_at = datetime.now(tz=UTC)
            body = resp.content
            results.append(
                RawFetchResult(
                    source_id=self.source_id,
                    fetched_at=fetched_at,
                    raw_content=body,
                    content_type=resp.headers.get("content-type", "application/json"),
                    parse_version="wfs_geojson_v1",
                    url=str(resp.url),
                ),
            )

            # Check pagination — WFS 2.0 uses numberReturned / numberMatched
            try:
                data = resp.json()
            except Exception:
                break

            returned = len(data.get("features", []))
            total_matched = data.get("numberMatched") or data.get("totalFeatures")

            if returned == 0:
                break
            start_index += returned

            # Stop if we got everything or fewer than requested
            if total_matched is not None and start_index >= total_matched:
                break
            if returned < self._config.max_features:
                break

        logger.info(
            "Paavo WFS: fetched %d page(s), postal_codes=%s",
            len(results),
            postal_codes,
        )
        return results

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse a Paavo WFS GeoJSON response into normalized records."""
        return self.normalize_statistics(raw)

    def normalize_statistics(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse WFS GeoJSON features into area-snapshot records.

        Each GeoJSON feature represents one postal-code area with dozens of
        statistical columns.  The most important fields are mapped to
        human-readable keys via :data:`_FIELD_MAP`; remaining ``"he_"``,
        ``"hr_"``, ``"ko_"``, ``"ra_"``, ``"te_"``-prefixed fields are kept
        under an ``"extra"`` sub-dict.
        """
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode Paavo WFS response from %s", raw.url)
            return []

        records: list[NormalizedRecord] = []

        for feature in data.get("features", []):
            props: dict[str, Any] = feature.get("properties", {})
            geometry = feature.get("geometry")

            postal_code = str(props.get("postinumeroalue", "") or props.get("posti_alue", "") or "").strip()
            source_record_id = postal_code or props.get("id") or str(uuid.uuid4())

            # Map known fields
            record_data: dict[str, Any] = {}
            extra: dict[str, Any] = {}

            for raw_key, raw_val in props.items():
                lower = raw_key.lower()
                if lower in _FIELD_MAP:
                    record_data[_FIELD_MAP[lower]] = raw_val
                elif any(lower.startswith(p) for p in ("he_", "hr_", "ko_", "ra_", "te_", "tp_", "pt_", "tr_")):
                    extra[raw_key] = raw_val

            if extra:
                record_data["extra"] = extra

            if geometry:
                record_data["geometry"] = geometry

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="area_stats",
                    source_record_id=str(source_record_id),
                    data=record_data,
                    fetched_at=raw.fetched_at,
                ),
            )

        logger.info("Paavo: normalized %d records from %s", len(records), raw.url)
        return records
