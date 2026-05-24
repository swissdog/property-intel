#!/usr/bin/env python3
"""Fetch full Paavo attribute set into property.paavo_attribute.

The PaavoConnector already pulls every he_/hr_/ko_/ra_/te_/tp_/pt_/tr_-
prefixed column from Tilastokeskus WFS into the ``extra`` dict on each
NormalizedRecord. This script flattens those into long-format rows
(one row per postal_code × year × attribute_code).

Idempotent: ON CONFLICT (postal_code, year, attribute_code) DO UPDATE.
Default mode fetches the current (default-layer) year for every postal
code; override --layer to backfill historical years.

Usage:
    python3 scripts/fetch_paavo_attributes.py
    python3 scripts/fetch_paavo_attributes.py --layer postialue:pno_tilasto_2024
    python3 scripts/fetch_paavo_attributes.py --year 2024 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jarvis_property_intel.connectors.paavo import PaavoConfig, PaavoConnector

logger = logging.getLogger("property-intel.paavo-attributes")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

# Paavo prefixes that we treat as numeric attributes
ATTRIBUTE_PREFIXES = ("he_", "hr_", "ko_", "ra_", "te_", "tp_", "pt_", "tr_")

# Year suffix in layer name, e.g. postialue:pno_tilasto_2024 → 2024
LAYER_YEAR_RE = re.compile(r"_(\d{4})$")


def _layer_year(layer: str, override: int | None) -> int:
    if override is not None:
        return override
    m = LAYER_YEAR_RE.search(layer)
    if m:
        return int(m.group(1))
    # Fallback: current year — operator should pass --year explicitly
    return datetime.now(UTC).year


def _coerce_number(raw: object) -> float | None:
    """Paavo returns numbers as int, float, or string ('-1' = suppressed)."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    # Tilastokeskus uses -1 to mark suppressed (n<5) cells; treat as NULL.
    if v == -1:
        return None
    return v


async def _existing_keys(engine, year: int) -> set[tuple[str, str]]:
    """Return the set of (postal_code, attribute_code) already present for *year*."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT postal_code, attribute_code FROM property.paavo_attribute "
                "WHERE year = :year"
            ),
            {"year": year},
        )
        return {(row.postal_code, row.attribute_code) for row in result.fetchall()}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--layer",
        default=os.getenv("PAAVO_WFS_LAYER", "postialue:pno_tilasto_2024"),
        help="WFS layer name (defaults to PAAVO_WFS_LAYER)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Override year tag (default: parsed from layer name)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip (postal_code, attribute_code) pairs already in DB for this year",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Force-overwrite existing rows (refresh values)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    year = _layer_year(args.layer, args.year)
    logger.info("Fetching Paavo attributes for layer=%s year=%d", args.layer, year)

    config = PaavoConfig(layer=args.layer)
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

    logger.info("Paavo: fetched %d features (across %d page(s))", len(features), len(raws))
    if not features:
        logger.error("No features returned — aborting")
        return 1

    if args.dry_run:
        # Diagnostic dump: show attribute count for the first feature
        sample = features[0].get("properties", {})
        attrs = [k for k in sample if k.lower().startswith(ATTRIBUTE_PREFIXES)]
        logger.info("DRY-RUN: %d numeric attributes per feature (sample: %s)",
                    len(attrs), attrs[:8])
        logger.info("DRY-RUN: would write up to %d rows", len(features) * len(attrs))
        return 0

    engine = create_async_engine(DB_URL, future=True)
    existing = await _existing_keys(engine, year) if args.skip_existing else set()
    if existing:
        logger.info("Skipping %d (pc, attr) pairs already present for year %d",
                    len(existing), year)

    upsert_sql = text(
        """
        INSERT INTO property.paavo_attribute
            (postal_code, year, attribute_code, attribute_label, value,
             source_layer, fetched_at)
        VALUES (:postal_code, :year, :attribute_code, :attribute_label, :value,
                :source_layer, now())
        ON CONFLICT (postal_code, year, attribute_code) DO UPDATE SET
            attribute_label = EXCLUDED.attribute_label,
            value           = EXCLUDED.value,
            source_layer    = EXCLUDED.source_layer,
            fetched_at      = EXCLUDED.fetched_at
        """
    )

    written = 0
    skipped = 0
    feature_count = 0
    async with engine.begin() as conn:
        for feature in features:
            props: dict = feature.get("properties", {})
            postal_code = str(
                props.get("postinumeroalue") or props.get("posti_alue") or ""
            ).strip()
            if not postal_code:
                continue
            feature_count += 1

            for raw_key, raw_val in props.items():
                lower = raw_key.lower()
                if not lower.startswith(ATTRIBUTE_PREFIXES):
                    continue
                if args.skip_existing and (postal_code, lower) in existing:
                    skipped += 1
                    continue
                value = _coerce_number(raw_val)
                if value is None:
                    continue
                await conn.execute(upsert_sql, {
                    "postal_code": postal_code,
                    "year": year,
                    "attribute_code": lower,
                    "attribute_label": raw_key,
                    "value": value,
                    "source_layer": args.layer,
                })
                written += 1

    await engine.dispose()
    logger.info(
        "Paavo attributes: %d rows written, %d skipped, %d features processed",
        written, skipped, feature_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
