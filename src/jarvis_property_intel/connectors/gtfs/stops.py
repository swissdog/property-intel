"""GTFS stop ingestion + transit-accessibility scoring.

GTFS static feeds ship a ``stops.txt`` CSV inside a zip. We only need the stop
coordinates to compute a per-asset public-transport accessibility proxy:

    score = 0.6 * walk_component + 0.4 * density_component

where walk_component rewards a short distance to the nearest stop (100 at the
doorstep, 0 at >=800 m) and density_component rewards many stops within 800 m
(100 at >=10 stops). The result is a 0-100 proxy — hence transit_score_proxy.

The pure functions (:func:`parse_stops_txt`, :func:`transit_access_score`) carry
no IO so they are unit-testable; :func:`download_gtfs_stops` fetches a feed.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# GTFS location_type values that represent a physical stop/platform a rider can
# board at (we exclude stations=1, entrances=2, generic nodes=3, boarding=4).
_BOARDABLE_LOCATION_TYPES = {"", "0"}


@dataclass(frozen=True, slots=True)
class TransitStop:
    feed: str
    stop_id: str
    name: str | None
    lat: float
    lon: float


def parse_stops_txt(content: str, feed: str) -> list[TransitStop]:
    """Parse a GTFS ``stops.txt`` CSV into boardable :class:`TransitStop` rows.

    Skips rows that are not boardable stops (stations/entrances/nodes) and rows
    with missing/invalid coordinates. Pure — no IO.
    """
    stops: list[TransitStop] = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        if (row.get("location_type") or "").strip() not in _BOARDABLE_LOCATION_TYPES:
            continue
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id:
            continue
        try:
            lat = float(row["stop_lat"])
            lon = float(row["stop_lon"])
        except (KeyError, ValueError, TypeError):
            continue
        # GTFS lat/lon must be sane WGS84; drop 0,0 and out-of-range junk.
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            continue
        name = (row.get("stop_name") or "").strip() or None
        stops.append(TransitStop(feed=feed, stop_id=stop_id, name=name, lat=lat, lon=lon))
    return stops


def transit_access_score(
    nearest_m: float | None, n_400m: int, n_800m: int
) -> float | None:
    """Combine nearest-stop distance + stop density into a 0-100 proxy.

    Returns None when there is no nearby stop (asset outside the feed's
    coverage) — callers should leave transit_score_proxy NULL in that case
    rather than reporting a misleading 0.
    """
    if nearest_m is None:
        return None
    walk = max(0.0, 100.0 * (1.0 - min(nearest_m, 800.0) / 800.0))
    density = min(100.0, n_800m * 10.0)
    return round(0.6 * walk + 0.4 * density, 1)


async def download_gtfs_stops(
    url: str, feed: str, *, timeout: float = 60.0
) -> list[TransitStop]:
    """Download a GTFS static zip and parse its ``stops.txt``."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.content
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        with zf.open("stops.txt") as f:
            text = io.TextIOWrapper(f, encoding="utf-8-sig").read()
    stops = parse_stops_txt(text, feed)
    logger.info("GTFS feed %r: parsed %d boardable stops from %s", feed, len(stops), url)
    return stops
