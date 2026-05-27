#!/usr/bin/env python3
"""Fetch GTFS stops and compute a per-asset transit-accessibility score.

Loads a GTFS static feed's stops into property.transit_stop, then computes
building_features.transit_score_proxy for every property_asset within the
feed's coverage (a stop within --coverage-m metres) via PostGIS. Assets
outside coverage are left untouched (NULL), never scored 0 misleadingly.

Currently ships the keyless HSL capital-region feed. Add more feeds (Waltti,
etc.) to FEEDS once a reliable keyless source is confirmed.

Idempotent: re-running refreshes stops and recomputes scores.

Usage:
    python3 scripts/fetch_transit_score.py
    python3 scripts/fetch_transit_score.py --feed hsl --coverage-m 2000
    python3 scripts/fetch_transit_score.py --skip-download   # reuse stored stops
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# Load repo-root .env (setdefault → never overrides an explicit env var) so both
# the systemd fetch unit and manual runs reach the real DB.
_ENV_FILE = os.path.join(_ROOT, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from jarvis_property_intel.connectors.gtfs import download_gtfs_stops, transit_access_score

logger = logging.getLogger("property-intel.transit-score")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

# Known keyless GTFS static feeds. Each feed declares the municipalities it
# actually serves comprehensively, so isolated cross-region stops in the feed
# (e.g. a single HSL long-distance stop in Lahti) don't produce a misleading
# partial score for a town the feed doesn't really cover. Municipalities are
# matched case-insensitively. HSL members as of 2026.
FEEDS: dict[str, dict] = {
    "hsl": {
        "url": os.getenv("GTFS_HSL_URL", "https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip"),
        "municipalities": {
            "helsinki", "espoo", "kauniainen", "vantaa",
            "kerava", "kirkkonummi", "sipoo", "siuntio", "tuusula",
        },
    },
}

# Per-asset spatial aggregation against the feed's stops, index-accelerated by
# the GiST index on transit_stop (geom::geography). Returns components only for
# assets that have at least one stop within :coverage metres (= in coverage).
_COMPONENTS_SQL = text("""
    SELECT a.asset_id,
           agg.nearest_m,
           agg.n400,
           agg.n800
    FROM property.property_asset a
    CROSS JOIN LATERAL (
        SELECT ST_SetSRID(ST_MakePoint(a.lon, a.lat), 4326)::geography AS g
    ) pt
    CROSS JOIN LATERAL (
        SELECT MIN(ST_Distance(s.geom::geography, pt.g)) AS nearest_m,
               COUNT(*) FILTER (WHERE ST_DWithin(s.geom::geography, pt.g, 400)) AS n400,
               COUNT(*) FILTER (WHERE ST_DWithin(s.geom::geography, pt.g, 800)) AS n800
        FROM property.transit_stop s
        WHERE s.feed = :feed
          AND ST_DWithin(s.geom::geography, pt.g, :coverage)
    ) agg
    WHERE a.lat IS NOT NULL AND a.lon IS NOT NULL
      AND LOWER(a.municipality) = ANY(:munis)
      AND agg.nearest_m IS NOT NULL
""")

_UPSERT_SCORE_SQL = text("""
    INSERT INTO property.building_features (asset_id, transit_score_proxy)
    VALUES (:asset_id, :score)
    ON CONFLICT (asset_id) DO UPDATE SET
        transit_score_proxy = EXCLUDED.transit_score_proxy
""")


async def load_stops(session: AsyncSession, feed: str, url: str) -> int:
    """Download a GTFS feed and replace property.transit_stop rows for it."""
    stops = await download_gtfs_stops(url, feed)
    if not stops:
        logger.warning("Feed %r returned no stops — leaving existing rows intact", feed)
        return 0
    # Atomic replace within the caller's transaction (no empty window outside tx).
    await session.execute(
        text("DELETE FROM property.transit_stop WHERE feed = :feed"), {"feed": feed}
    )
    await session.execute(
        text("""
            INSERT INTO property.transit_stop (feed, stop_id, name, lat, lon, geom)
            VALUES (:feed, :stop_id, :name, :lat, :lon,
                    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        """),
        [
            {"feed": s.feed, "stop_id": s.stop_id, "name": s.name, "lat": s.lat, "lon": s.lon}
            for s in stops
        ],
    )
    return len(stops)


async def score_assets(
    session: AsyncSession, feed: str, coverage_m: float, municipalities: set[str]
) -> dict[str, int]:
    """Compute + upsert transit_score_proxy for assets within feed coverage.

    Resets transit_score_proxy to NULL first so assets that fall out of coverage
    (or out of the feed's served municipalities) don't keep a stale score. v1 is
    single-feed; multi-feed would need per-feed score columns instead.
    """
    await session.execute(
        text("UPDATE property.building_features SET transit_score_proxy = NULL "
             "WHERE transit_score_proxy IS NOT NULL")
    )
    rows = (await session.execute(
        _COMPONENTS_SQL,
        {"feed": feed, "coverage": coverage_m, "munis": sorted(municipalities)},
    )).fetchall()

    params = []
    for asset_id, nearest_m, n400, n800 in rows:
        score = transit_access_score(
            float(nearest_m) if nearest_m is not None else None, int(n400), int(n800)
        )
        if score is not None:
            params.append({"asset_id": asset_id, "score": score})

    if params:
        await session.execute(_UPSERT_SCORE_SQL, params)
    return {"in_coverage": len(rows), "scored": len(params)}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", default="hsl", help="Feed key (see FEEDS)")
    parser.add_argument("--coverage-m", type=float, default=2000.0,
                        help="Max metres to nearest stop to be 'in coverage'")
    parser.add_argument("--skip-download", action="store_true",
                        help="Reuse stops already in transit_stop")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.feed not in FEEDS:
        logger.error("Unknown feed %r (known: %s)", args.feed, ", ".join(FEEDS))
        return 2

    engine = create_async_engine(DB_URL, future=True)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionFactory() as session:
            feed_cfg = FEEDS[args.feed]
            if not args.skip_download:
                n = await load_stops(session, args.feed, feed_cfg["url"])
                logger.info("Loaded %d stops for feed %r", n, args.feed)
            stats = await score_assets(
                session, args.feed, args.coverage_m, feed_cfg["municipalities"]
            )
            await session.commit()
            logger.info(
                "Transit score: %d assets in coverage, %d scored (feed=%s)",
                stats["in_coverage"], stats["scored"], args.feed,
            )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
