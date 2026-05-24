#!/usr/bin/env python3
"""One-shot: backfill postal_code (and lat/lon) for property_asset rows
that have neither. Uses Oikotie /api/card/{id} detail endpoint, which
includes address.zipCode.name and address.coordinates.

Selection criterion: property_asset rows where postal_code is empty AND
lat/lon are NULL, and which are joined to a property.listing with
source='oikotie'. Idempotent and rate-limited.

Usage:
    python3 scripts/backfill_missing_postal_via_detail.py
    python3 scripts/backfill_missing_postal_via_detail.py --dry-run
    python3 scripts/backfill_missing_postal_via_detail.py --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jarvis_property_intel.connectors.oikotie import OikotieConfig, OikotieConnector

logger = logging.getLogger("property-intel.backfill-detail")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


def _extract_from_detail(card: dict) -> tuple[str | None, float | None, float | None]:
    """Pull postal_code, lat, lon out of an Oikotie /api/card/{id} payload."""
    address = card.get("address", {}) or {}
    ad_data = card.get("adData", {}) or {}

    postal_code: str | None = None
    zip_obj = address.get("zipCode")
    if isinstance(zip_obj, dict):
        postal_code = zip_obj.get("name") or zip_obj.get("value")
    elif isinstance(zip_obj, str):
        postal_code = zip_obj
    if not postal_code:
        postal_code = ad_data.get("zipCodeInfo")

    coords = address.get("coordinates") or card.get("coordinates") or {}
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    try:
        lat_f = float(lat) if lat is not None else None
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat_f, lon_f = None, None
    return (postal_code or None), lat_f, lon_f


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_async_engine(DB_URL, future=True)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT pa.asset_id::text AS asset_id, l.source_listing_id
                    FROM property.property_asset pa
                    JOIN property.listing l ON l.asset_id = pa.asset_id
                    WHERE l.source = 'oikotie'
                      AND (pa.postal_code IS NULL OR pa.postal_code = '')
                      AND (pa.lat IS NULL OR pa.lon IS NULL)
                    """
                )
            )
        ).mappings().all()
    logger.info("Found %d rows to backfill", len(rows))
    if args.limit:
        rows = rows[: args.limit]

    connector = OikotieConnector(OikotieConfig())
    stats = {"fetched": 0, "updated": 0, "no_postal": 0, "fetch_failed": 0, "polygon_fallback": 0}

    try:
        for r in rows:
            card_id = r["source_listing_id"]
            try:
                detail_raw = await connector.fetch_detail(int(card_id))
            except Exception:
                logger.exception("Detail fetch raised for %s", card_id)
                detail_raw = None
            if not detail_raw:
                stats["fetch_failed"] += 1
                continue
            try:
                card = json.loads(detail_raw.raw_content)
            except Exception:
                stats["fetch_failed"] += 1
                continue
            stats["fetched"] += 1

            postal, lat, lon = _extract_from_detail(card)

            # Final fallback: reverse-geocode if we got coords but no postal
            if not postal and lat is not None and lon is not None:
                async with engine.connect() as c2:
                    res = await c2.execute(
                        text("SELECT property.lookup_postal_code(:lat, :lon)"),
                        {"lat": lat, "lon": lon},
                    )
                    postal = res.scalar()
                    if postal:
                        stats["polygon_fallback"] += 1

            if not postal and lat is None and lon is None:
                stats["no_postal"] += 1
                continue

            if args.dry_run:
                logger.info(
                    "DRY: %s id=%s pc=%s lat=%s lon=%s",
                    r["asset_id"], card_id, postal, lat, lon,
                )
                continue

            async with engine.begin() as c2:
                await c2.execute(
                    text(
                        """
                        UPDATE property.property_asset
                        SET
                            postal_code = COALESCE(NULLIF(:postal, ''), postal_code),
                            lat = COALESCE(:lat, lat),
                            lon = COALESCE(:lon, lon)
                        WHERE asset_id = :asset_id
                        """
                    ),
                    {"postal": postal or "", "lat": lat, "lon": lon, "asset_id": r["asset_id"]},
                )
            stats["updated"] += 1

            if stats["fetched"] % 25 == 0:
                logger.info("Progress: %s", stats)
    finally:
        await connector.close()
        await engine.dispose()

    logger.info("Backfill complete: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
