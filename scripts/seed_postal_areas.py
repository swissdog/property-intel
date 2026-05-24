#!/usr/bin/env python3
"""Seed property.postal_code_area from Tilastokeskus Paavo WFS.

Fetches all Finnish postal-code area polygons (~3000) and upserts them
into property.postal_code_area for use as reverse-geocoding lookup table.

Idempotent: safe to re-run. Existing rows are updated (geometry refreshed).

Usage:
    python3 scripts/seed_postal_areas.py
    python3 scripts/seed_postal_areas.py --dry-run
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

from jarvis_property_intel.connectors.paavo import PaavoConfig, PaavoConnector

logger = logging.getLogger("property-intel.seed-postal-areas")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


async def fetch_all_postal_polygons(config: PaavoConfig) -> list[dict]:
    """Fetch every postal-code polygon from Paavo WFS (no filter)."""
    connector = PaavoConnector(config)
    try:
        raws = await connector.fetch_dataset()
    finally:
        await connector.close()

    features: list[dict] = []
    for raw in raws:
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode Paavo response from %s", raw.url)
            continue
        features.extend(data.get("features", []))
    logger.info("Paavo: fetched %d postal-area features across %d page(s)", len(features), len(raws))
    return features


async def upsert_polygons(features: list[dict], dry_run: bool, layer: str) -> int:
    """Upsert features into property.postal_code_area. Returns row count written."""
    if dry_run:
        logger.info("DRY-RUN: would upsert %d features", len(features))
        return 0

    engine = create_async_engine(DB_URL, future=True)
    written = 0
    skipped = 0
    async with engine.begin() as conn:
        for feature in features:
            props = feature.get("properties", {})
            geom = feature.get("geometry")
            postal_code = str(
                props.get("postinumeroalue") or props.get("posti_alue") or ""
            ).strip()
            if not postal_code or geom is None:
                skipped += 1
                continue
            name = str(props.get("nimi") or "")[:200] or None
            municipality_code = str(props.get("kunta") or "")[:10] or None
            municipality_name = str(props.get("kuntanimi") or "")[:100] or None

            await conn.execute(
                text(
                    """
                    INSERT INTO property.postal_code_area
                        (postal_code, name, municipality_code, municipality_name, geom, fetched_at, source_layer)
                    VALUES (
                        :postal_code, :name, :municipality_code, :municipality_name,
                        ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)),
                        now(), :layer
                    )
                    ON CONFLICT (postal_code) DO UPDATE SET
                        name              = EXCLUDED.name,
                        municipality_code = EXCLUDED.municipality_code,
                        municipality_name = EXCLUDED.municipality_name,
                        geom              = EXCLUDED.geom,
                        fetched_at        = EXCLUDED.fetched_at,
                        source_layer      = EXCLUDED.source_layer
                    """
                ),
                {
                    "postal_code": postal_code,
                    "name": name,
                    "municipality_code": municipality_code,
                    "municipality_name": municipality_name,
                    "geom": json.dumps(geom),
                    "layer": layer,
                },
            )
            written += 1
    await engine.dispose()
    logger.info("Upserted %d polygons (skipped %d malformed)", written, skipped)
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write")
    parser.add_argument(
        "--layer",
        default=os.getenv("PAAVO_WFS_LAYER", "postialue:pno_tilasto_2024"),
        help="WFS layer name",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = PaavoConfig(layer=args.layer)
    features = await fetch_all_postal_polygons(config)
    if not features:
        logger.error("No features returned from Paavo WFS — aborting")
        return 1

    written = await upsert_polygons(features, dry_run=args.dry_run, layer=args.layer)
    logger.info("Seed complete: %d rows", written)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
