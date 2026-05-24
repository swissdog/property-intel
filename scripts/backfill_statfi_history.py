#!/usr/bin/env python3
"""One-shot: backfill StatFi apartment-price history from 2020-Q1 onwards.

The hourly pipeline only fetches the last 4 quarters. This script fetches
all quarters from 2020-Q1 through the latest published quarter (~Q4 2025
given typical 2-quarter publishing lag), giving 5+ years of postal-code-
level median price + transaction-volume aggregates.

Idempotent: the area_snapshot uq_area_snapshot_period UNIQUE constraint
upserts existing rows.

Usage:
    python3 scripts/backfill_statfi_history.py
    python3 scripts/backfill_statfi_history.py --from 2020 --to 2026
    python3 scripts/backfill_statfi_history.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import httpx
from jarvis_property_intel.connectors.statfi import StatFiConfig, StatFiPxWebConnector
# Reuse the pipeline's writer
from scripts.hourly_pipeline import write_statfi_to_db

logger = logging.getLogger("property-intel.backfill-statfi")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


def _all_quarters(from_year: int, to_year: int) -> list[str]:
    qs: list[str] = []
    for y in range(from_year, to_year + 1):
        for q in (1, 2, 3, 4):
            qs.append(f"{y}Q{q}")
    return qs


async def _published_quarters(config: StatFiConfig) -> set[str] | None:
    """Probe the StatFi table metadata for the actually-published quarter list.

    Returns the set of valid 'YYYYQ#' codes, or None if metadata fetch fails
    (in which case caller should fall back to its full requested range).
    """
    url = f"{config.base_url}/{config.apartment_prices_table}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            meta = resp.json()
    except Exception:
        logger.warning("Could not probe StatFi metadata for valid quarters")
        return None
    for v in meta.get("variables", []):
        if v.get("code") == "Vuosineljännes":
            return set(v.get("values", []))
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_year", type=int, default=2020)
    parser.add_argument("--to", dest="to_year", type=int, default=date.today().year)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=8,
                        help="Quarters per StatFi POST (PxWeb has size limits)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    requested = _all_quarters(args.from_year, args.to_year)
    config = StatFiConfig()

    # Cap to actually-published quarters so we don't get 400 on future periods.
    published = await _published_quarters(config)
    if published is not None:
        quarters = [q for q in requested if q in published]
        skipped = len(requested) - len(quarters)
        if skipped:
            logger.info("Skipping %d unpublished quarter(s) outside StatFi's domain", skipped)
    else:
        quarters = requested

    if not quarters:
        logger.info("No quarters to fetch (all requested are unpublished)")
        return 0
    logger.info("StatFi backfill range: %s → %s (%d quarters)", quarters[0], quarters[-1], len(quarters))

    if args.dry_run:
        for q in quarters:
            print(q)
        return 0

    engine = create_async_engine(DB_URL, future=True)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    connector = StatFiPxWebConnector(config)

    total_records = 0
    total_written = 0
    try:
        if not await connector.health_check():
            logger.error("StatFi health-check failed")
            return 1

        for i in range(0, len(quarters), args.batch):
            batch = quarters[i : i + args.batch]
            logger.info("Fetching batch %s", batch)
            try:
                results = await connector.fetch_dataset(
                    dataset_id="apartment_prices",
                    query={
                        "query": [
                            {
                                "code": "Vuosineljännes",
                                "selection": {"filter": "item", "values": batch},
                            },
                            {
                                "code": "Talotyyppi",
                                "selection": {"filter": "item", "values": ["1", "2", "3", "5"]},
                            },
                        ],
                        "response": {"format": "json-stat2"},
                    },
                )
            except Exception:
                logger.exception("StatFi fetch failed for batch %s", batch)
                continue

            records = []
            for raw in results:
                try:
                    records.extend(connector.normalize(raw))
                except Exception:
                    logger.exception("Normalize failed for batch %s", batch)
            if not records:
                logger.warning("Batch %s returned 0 records", batch)
                continue
            total_records += len(records)

            async with SessionFactory() as session:
                try:
                    written = await write_statfi_to_db(session, records)
                    await session.commit()
                    total_written += written
                    logger.info("Batch %s: %d records → %d rows written",
                                batch, len(records), written)
                except Exception:
                    logger.exception("DB write failed for batch %s", batch)
    finally:
        await connector.close()
        await engine.dispose()

    logger.info("StatFi backfill complete: %d records normalized, %d rows written",
                total_records, total_written)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
