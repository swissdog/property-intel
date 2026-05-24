#!/usr/bin/env python3
"""One-shot: backfill detail-fetched fields for ALL active oikotie listings.

Calls Oikotie /api/card/{id} for every active listing where
detail_fetched_at IS NULL. Rate-limited to ~1 req/s; takes about
3 hours for ~10k listings on first run. Idempotent and resumable
(re-running picks up where it left off).

Usage:
    python3 scripts/backfill_listing_details.py
    python3 scripts/backfill_listing_details.py --batch 500    # commit every N
    python3 scripts/backfill_listing_details.py --limit 100    # smoke-test small slice
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Reuse the pipeline's enrichment function
from scripts.hourly_pipeline import enrich_oikotie_details

logger = logging.getLogger("property-intel.backfill-details")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=200, help="Commit every N listings")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N total")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_async_engine(DB_URL, future=True)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Show pending count
    async with SessionFactory() as session:
        pending = (await session.execute(text(
            "SELECT COUNT(*) FROM property.listing "
            "WHERE source='oikotie' AND status='active' AND detail_fetched_at IS NULL"
        ))).scalar_one()
    logger.info("Pending listings to enrich: %d", pending)

    total = {"fetched": 0, "updated": 0, "fetch_failed": 0}
    processed = 0
    while True:
        budget = args.batch
        if args.limit is not None:
            remaining = args.limit - processed
            if remaining <= 0:
                break
            budget = min(budget, remaining)

        async with SessionFactory() as session:
            stats = await enrich_oikotie_details(session, max_records=budget)
            await session.commit()

        if stats["updated"] == 0 and stats["fetched"] == 0:
            break  # No more pending rows

        for k in total:
            total[k] += stats[k]
        processed += stats["fetched"] + stats["fetch_failed"]
        logger.info(
            "Batch: fetched=%d updated=%d failed=%d | running total=%s | processed=%d/%d",
            stats["fetched"], stats["updated"], stats["fetch_failed"], total, processed, pending,
        )

    await engine.dispose()
    logger.info("Backfill complete: %s", total)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
