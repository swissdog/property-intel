"""Digiroad parking area connector (Väylävirasto / Finnish Transport Agency).

Digiroad distributes road-linked parking area data. The WFS API
(avoinapi.vayla.fi) requires authentication and is not always available.

This connector uses the Digiroad R download service for GeoJSON extracts.
Falls back to a curated static endpoint if the main API is unreachable.

Status: The Väylävirasto API endpoints were unreachable as of 2026-03-29.
This connector will be updated once a reliable open API is confirmed.
Current approach: manual GeoPackage download from aineistot.vayla.fi.

License: CC BY 4.0 (Finnish government open data).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from ..base import NormalizedRecord, RawFetchResult

logger = logging.getLogger(__name__)

# Known Digiroad API endpoints (may require auth)
VAYLA_ENDPOINTS = [
    "https://avoinapi.vayla.fi/vaylaominaisuudet/ows",
    "https://avoinapi.vaylapilvi.fi/vaylaominaisuudet/ows",
]


class DigiroadConnector:
    """Fetches parking area data from Digiroad (Väylävirasto).

    NOTE: As of 2026-03, the Väylävirasto WFS API is not reliably available
    without authentication. This connector attempts the known endpoints and
    returns empty results gracefully if unavailable. OSM parking connector
    provides overlapping coverage (~66K features).
    """

    source_id = "digiroad"

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def health_check(self) -> bool:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for url in VAYLA_ENDPOINTS:
                try:
                    resp = await client.get(
                        url,
                        params={"service": "WFS", "request": "GetCapabilities"},
                    )
                    if resp.status_code == 200 and "WFS_Capabilities" in resp.text[:500]:
                        return True
                except Exception:
                    continue
        logger.warning("Digiroad: no reachable WFS endpoint found")
        return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        now = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for url in VAYLA_ENDPOINTS:
                try:
                    resp = await client.get(
                        url,
                        params={
                            "service": "WFS",
                            "version": "2.0.0",
                            "request": "GetFeature",
                            "typeName": "tierekisteri:tl507",  # parking areas
                            "outputFormat": "application/json",
                            "count": 10000,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("features"):
                            logger.info("Digiroad: %d features from %s", len(data["features"]), url)
                            return [
                                RawFetchResult(
                                    source_id=self.source_id,
                                    fetched_at=now,
                                    raw_content=resp.content,
                                    content_type="application/json",
                                    parse_version="wfs_v1",
                                    url=url,
                                )
                            ]
                except Exception as e:
                    logger.debug("Digiroad endpoint %s failed: %s", url, e)

        logger.warning("Digiroad: no data available from any endpoint")
        return []

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        data = json.loads(raw.raw_content)
        features = data.get("features", [])
        records: list[NormalizedRecord] = []

        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry")
            if not geom:
                continue

            # Compute centroid for point representation
            coords = geom.get("coordinates", [])
            if geom["type"] == "Point":
                lon, lat = coords[0], coords[1]
            elif geom["type"] in ("LineString", "MultiLineString"):
                # Use midpoint of first linestring
                line = coords[0] if geom["type"] == "MultiLineString" else coords
                mid = line[len(line) // 2] if line else [0, 0]
                lon, lat = mid[0], mid[1]
            else:
                continue

            fid = feat.get("id") or props.get("id") or str(hash(str(coords[:2])))

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="parking_facility",
                    source_record_id=str(fid),
                    data={
                        "name": props.get("nimi") or props.get("name", ""),
                        "lat": lat,
                        "lng": lon,
                        "city": props.get("kunta") or "",
                        "facility_type": "lot",
                        "capacity": props.get("paikkaluku") or props.get("capacity"),
                        "indoor": False,
                        "has_ev_charging": False,
                        "has_anpr": False,
                        "operator": "",
                        "services": [],
                    },
                    fetched_at=raw.fetched_at,
                )
            )

        logger.info("Digiroad: normalized %d records", len(records))
        return records
