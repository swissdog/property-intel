#!/usr/bin/env python3
"""Seed property.municipality_region from Tilastokeskus classification service.

Resolves Finnish municipalities (kunta, 3-digit codes) to their parent
maakunta (2-digit codes). Used to join migration_activity (KU091…) to
construction_activity (MK01…).

Source: api.stat.fi correspondence tables. Re-run yearly when the
classification baseline year changes (default: 2025).

Usage:
    python3 scripts/seed_municipality_region.py
    python3 scripts/seed_municipality_region.py --year 2024
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("property-intel.muniseed")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


async def fetch_correspondence(client: httpx.AsyncClient, year: int) -> list[dict]:
    """Fetch kunta→maakunta correspondence map for the given year."""
    base = "https://api.stat.fi/classificationservice/open/api/classifications/v2"
    url = f"{base}/correspondenceTables/kunta_1_{year}0101%23maakunta_1_{year}0101/maps"
    resp = await client.get(url, params={"content": "data", "meta": "min"}, timeout=60)
    resp.raise_for_status()
    return resp.json()


async def fetch_classification_labels(
    client: httpx.AsyncClient, classification: str
) -> dict[str, str]:
    """Fetch code→label map for a single classification (kunta or maakunta)."""
    base = "https://api.stat.fi/classificationservice/open/api/classifications/v2"
    url = f"{base}/classifications/{classification}/classificationItems"
    resp = await client.get(url, params={"content": "data", "meta": "min"}, timeout=60)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for item in resp.json():
        code = item.get("code", "")
        names = item.get("classificationItemNames") or []
        # Pick Finnish name if available
        name = None
        for n in names:
            if n.get("languageCode") == "fi":
                name = n.get("name")
                break
        if not name and names:
            name = names[0].get("name")
        if code and name:
            out[code] = name
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    async with httpx.AsyncClient() as client:
        logger.info("Fetching kunta→maakunta correspondence for %d…", args.year)
        rows = await fetch_correspondence(client, args.year)
        logger.info("  %d mappings", len(rows))
        kunta_names = await fetch_classification_labels(client, f"kunta_1_{args.year}0101")
        maakunta_names = await fetch_classification_labels(client, f"maakunta_1_{args.year}0101")
        logger.info("  %d kunta names, %d maakunta names", len(kunta_names), len(maakunta_names))

    engine = create_async_engine(DB_URL, future=True)
    upsert_sql = text(
        """
        INSERT INTO property.municipality_region
            (municipality_code, municipality_name, region_code, region_name, classification_year, fetched_at)
        VALUES (:m_code, :m_name, :r_code, :r_name, :yr, now())
        ON CONFLICT (municipality_code) DO UPDATE SET
            municipality_name   = EXCLUDED.municipality_name,
            region_code         = EXCLUDED.region_code,
            region_name         = EXCLUDED.region_name,
            classification_year = EXCLUDED.classification_year,
            fetched_at          = now()
        """
    )

    written = 0
    async with engine.begin() as conn:
        for r in rows:
            kunta_code = r["sourceLocalId"].split("/")[-1]   # e.g. '091'
            maakunta_code = r["targetLocalId"].split("/")[-1]  # e.g. '01'
            await conn.execute(upsert_sql, {
                "m_code": kunta_code,
                "m_name": kunta_names.get(kunta_code),
                "r_code": maakunta_code,
                "r_name": maakunta_names.get(maakunta_code),
                "yr": args.year,
            })
            written += 1
    await engine.dispose()
    logger.info("Done: %d rows upserted", written)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
