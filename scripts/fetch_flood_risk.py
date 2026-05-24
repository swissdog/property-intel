#!/usr/bin/env python3
"""Seed property.flood_risk_area from SYKE INSPIRE flood-hazard WFS.

Fetches every polygon for each configured scenario (100y / 250y /
significant) and upserts on (scenario, source_feature_id). The table
is replaceable on each run — geometry is stable but SYKE may revise
attributes between releases (every 6 years per EU directive).

For full reseed (drop & re-pull), pass --truncate. Default mode is
upsert which is safe but leaves orphan rows when a scenario is removed
upstream.

Usage:
    python3 scripts/fetch_flood_risk.py
    python3 scripts/fetch_flood_risk.py --scenario 100y
    python3 scripts/fetch_flood_risk.py --truncate
    python3 scripts/fetch_flood_risk.py --dry-run
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

from jarvis_property_intel.connectors.syke_flood import SykeFloodConfig, SykeFloodConnector

logger = logging.getLogger("property-intel.flood-risk")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=["100y", "250y", "significant"],
        default=None,
        help="Limit fetch to one scenario (default: all configured layers)",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Drop existing rows for the targeted scenario(s) before insert",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch but do not write")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = SykeFloodConfig()
    connector = SykeFloodConnector(config)
    try:
        raws = await connector.fetch_dataset(dataset_id=args.scenario)
    finally:
        await connector.close()

    if not raws:
        logger.error("No WFS pages returned — aborting (check SYKE service availability)")
        return 1

    # Decode all features grouped by scenario
    scenarios: dict[str, list[tuple[str, dict, dict]]] = {}
    for raw in raws:
        scenario = raw.source_record_id or "unknown"
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode SYKE response from %s", raw.url)
            continue
        for feature in data.get("features", []):
            geom = feature.get("geometry")
            if not geom:
                continue
            feature_id = str(
                feature.get("id")
                or feature.get("properties", {}).get("inspireId")
                or feature.get("properties", {}).get("OBJECTID")
                or ""
            )
            if not feature_id:
                continue
            scenarios.setdefault(scenario, []).append(
                (feature_id, feature.get("properties", {}) or {}, geom)
            )

    if args.dry_run:
        for scenario, items in scenarios.items():
            logger.info("DRY-RUN: %s → %d polygons", scenario, len(items))
        return 0

    engine = create_async_engine(DB_URL, future=True)
    total_written = 0
    total_truncated = 0

    upsert_sql = text(
        """
        INSERT INTO property.flood_risk_area
            (scenario, source_layer, source_feature_id, properties, geom, fetched_at)
        VALUES (
            :scenario, :source_layer, :source_feature_id,
            CAST(:properties AS jsonb),
            ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326)),
            now()
        )
        ON CONFLICT (scenario, source_feature_id) DO UPDATE SET
            source_layer = EXCLUDED.source_layer,
            properties   = EXCLUDED.properties,
            geom         = EXCLUDED.geom,
            fetched_at   = EXCLUDED.fetched_at
        """
    )

    async with engine.begin() as conn:
        if args.truncate:
            for scenario in scenarios:
                result = await conn.execute(
                    text("DELETE FROM property.flood_risk_area WHERE scenario = :s"),
                    {"s": scenario},
                )
                total_truncated += result.rowcount or 0

        for scenario, items in scenarios.items():
            # Find the configured layer's REST path for this scenario (for source_layer)
            source_layer = next(
                (f"{ly.service}/MapServer/{ly.layer_id}"
                 for ly in config.layers if ly.scenario == scenario),
                scenario,
            )
            written = 0
            skipped = 0
            for feature_id, props, geom in items:
                try:
                    await conn.execute(upsert_sql, {
                        "scenario": scenario,
                        "source_layer": source_layer,
                        "source_feature_id": feature_id[:120],
                        "properties": json.dumps(props),
                        "geom": json.dumps(geom),
                    })
                    written += 1
                except Exception:
                    logger.exception(
                        "Failed to upsert %s/%s — skipping",
                        scenario, feature_id,
                    )
                    skipped += 1
            total_written += written
            logger.info(
                "Scenario %s: %d written, %d skipped",
                scenario, written, skipped,
            )

    await engine.dispose()
    logger.info(
        "Flood-risk seed done: %d rows written, %d truncated",
        total_written, total_truncated,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
