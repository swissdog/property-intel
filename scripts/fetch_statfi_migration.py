#!/usr/bin/env python3
"""Fetch StatFi migration data into property.migration_activity.

Source: StatFin/muutl/statfin_muutl_pxt_11ae.px (Väestönmuutokset alueittain)
Granularity: municipality, yearly, 1990-2024 (35 years).

Idempotent.

Usage:
    python3 scripts/fetch_statfi_migration.py
    python3 scripts/fetch_statfi_migration.py --from 2015 --to 2024
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("property-intel.migration")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

PXWEB_TABLE = "https://pxdata.stat.fi/PXWeb/api/v1/fi/StatFin/muutl/statfin_muutl_pxt_11ae.px"

MEASURES = [
    "vaesto", "valisays", "luonvalisays",
    "vm43_tulo", "vm43_lahto", "vm43_netto",
    "vm41", "vm42", "vm4142", "koknetmuutto",
]


def _decode_jsonstat(data: dict) -> list[dict]:
    dim_ids = data.get("id", [])
    sizes = data.get("size", [])
    dimensions = data.get("dimension", {})
    values = data.get("value", [])
    dim_keys = []
    for did in dim_ids:
        cat = dimensions.get(did, {}).get("category", {})
        idx = cat.get("index", {})
        dim_keys.append(sorted(idx, key=lambda k: idx[k]))
    out = []
    for flat_idx, v in enumerate(values):
        if v is None:
            continue
        rem = flat_idx
        indices = []
        for s in reversed(sizes):
            indices.append(rem % s)
            rem //= s
        indices.reverse()
        row = {"value": v}
        for i, did in enumerate(dim_ids):
            if indices[i] < len(dim_keys[i]):
                row[did] = dim_keys[i][indices[i]]
        out.append(row)
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_year", type=int, default=2015)
    parser.add_argument("--to", dest="to_year", type=int, default=2024)
    parser.add_argument("--force-full", action="store_true",
                        help="Refetch all years even if already in DB")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    engine = create_async_engine(DB_URL, future=True)

    # Skip-existing: StatFi 11ae releases yearly. If we already have all years
    # in [from..to] in the DB, no point hammering the API on every cron run.
    if args.force_full:
        skip_years: set[int] = set()
    else:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT DISTINCT period_year FROM property.migration_activity"))
            skip_years = {int(r[0]) for r in result.fetchall()}

    requested_years = [y for y in range(args.from_year, args.to_year + 1) if y not in skip_years]
    if not requested_years:
        logger.info("All %d years already cached — nothing to fetch",
                    args.to_year - args.from_year + 1)
        await engine.dispose()
        return 0
    years = [str(y) for y in requested_years]
    logger.info("Fetching %d new years (skipped %d cached): %s",
                len(years), args.to_year - args.from_year + 1 - len(years), years)
    # 309 areas × 10 measures × 10 years = ~31k cells; chunk by 5 years.
    batch_size = 5
    upsert_sql = text(
        """
        INSERT INTO property.migration_activity
            (municipality_code, period_year, population_year_end, natural_increase,
             inter_muni_in, inter_muni_out, inter_muni_net,
             intl_immigration, intl_emigration, intl_net,
             total_net_migration, pop_change, fetched_at)
        VALUES (:code, :yr, :pop, :nat,
                :in_in, :in_out, :in_net,
                :imm, :emi, :imm_net,
                :tot_net, :pop_chg, now())
        ON CONFLICT (municipality_code, period_year) DO UPDATE SET
            population_year_end  = EXCLUDED.population_year_end,
            natural_increase     = EXCLUDED.natural_increase,
            inter_muni_in        = EXCLUDED.inter_muni_in,
            inter_muni_out       = EXCLUDED.inter_muni_out,
            inter_muni_net       = EXCLUDED.inter_muni_net,
            intl_immigration     = EXCLUDED.intl_immigration,
            intl_emigration      = EXCLUDED.intl_emigration,
            intl_net             = EXCLUDED.intl_net,
            total_net_migration  = EXCLUDED.total_net_migration,
            pop_change           = EXCLUDED.pop_change,
            fetched_at           = now()
        """
    )

    total_written = 0
    async with httpx.AsyncClient() as client:
        for i in range(0, len(years), batch_size):
            batch = years[i : i + batch_size]
            logger.info("Years %s..%s", batch[0], batch[-1])
            query = {
                "query": [
                    {"code": "Vuosi",  "selection": {"filter": "item", "values": batch}},
                    {"code": "Alue",    "selection": {"filter": "all", "values": ["*"]}},
                    {"code": "Tiedot",  "selection": {"filter": "item", "values": MEASURES}},
                ],
                "response": {"format": "json-stat2"},
            }
            for attempt in range(4):
                resp = await client.post(PXWEB_TABLE, json=query, timeout=120)
                if resp.status_code == 200:
                    break
                if resp.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("PxWeb %d: %s", resp.status_code, resp.text[:200])
                resp = None
                break
            if not resp or resp.status_code != 200:
                continue
            rows = _decode_jsonstat(resp.json())

            grouped: dict[tuple[str, str], dict] = {}
            for r in rows:
                code = r.get("Alue", "")
                yr = r.get("Vuosi", "")
                measure = r.get("Tiedot", "")
                if not code or not yr or not measure:
                    continue
                grouped.setdefault((code, yr), {})[measure] = r["value"]

            async with engine.begin() as conn:
                written = 0
                for (code, yr), m in grouped.items():
                    await conn.execute(upsert_sql, {
                        "code": code,
                        "yr": int(yr),
                        "pop":      int(m["vaesto"])      if m.get("vaesto") is not None else None,
                        "nat":      int(m["luonvalisays"])if m.get("luonvalisays") is not None else None,
                        "in_in":    int(m["vm43_tulo"])   if m.get("vm43_tulo") is not None else None,
                        "in_out":   int(m["vm43_lahto"])  if m.get("vm43_lahto") is not None else None,
                        "in_net":   int(m["vm43_netto"])  if m.get("vm43_netto") is not None else None,
                        "imm":      int(m["vm41"])        if m.get("vm41") is not None else None,
                        "emi":      int(m["vm42"])        if m.get("vm42") is not None else None,
                        "imm_net":  int(m["vm4142"])      if m.get("vm4142") is not None else None,
                        "tot_net":  int(m["koknetmuutto"])if m.get("koknetmuutto") is not None else None,
                        "pop_chg":  int(m["valisays"])    if m.get("valisays") is not None else None,
                    })
                    written += 1
                total_written += written
            logger.info("  → %d rows written", written)
            await asyncio.sleep(1.0)

    await engine.dispose()
    logger.info("Done: %d rows total", total_written)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
