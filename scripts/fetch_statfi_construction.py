#!/usr/bin/env python3
"""Fetch StatFi construction activity (permits / starts / completions) at maakunta level.

Source: StatFin/raku/statfin_raku_pxt_156f.px (1995M01-current).
Dimensions: rakennusvaihe × alue × timeperiod × rakennusluokitus2018 × ContentCode.

Granularity: maakunta (region), monthly. 20 regions × ~370 months × 3 phases
× 24 classes × 8 content codes = ~3.4M cells; we narrow to dwelling-relevant
phases + content codes to keep response sizes manageable.

Idempotent.

Usage:
    python3 scripts/fetch_statfi_construction.py
    python3 scripts/fetch_statfi_construction.py --from 2020 --to 2026
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

logger = logging.getLogger("property-intel.construction")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

PXWEB_TABLE = "https://pxdata.stat.fi/PXWeb/api/v1/fi/StatFin/raku/156f.px"

PHASE_LABEL = {"1": "permit", "2": "start", "3": "completion"}


async def _published_months() -> set[str] | None:
    """Probe table metadata for the actually-published timeperiod values.

    Returns the set of valid 'YYYYMmm' codes, or None if metadata fetch fails.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(PXWEB_TABLE)
            resp.raise_for_status()
            meta = resp.json()
    except Exception:
        logger.warning("Could not probe StatFi 156f metadata for valid months")
        return None
    for v in meta.get("variables", []):
        if v.get("code") == "timeperiod_m":
            return set(v.get("values", []))
    return None


def _months(start_year: int, end_year: int) -> list[str]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(f"{y}M{m:02d}")
    return out


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


async def _fetch(client: httpx.AsyncClient, query: dict) -> dict | None:
    delay = 2.0
    for _ in range(4):
        resp = await client.post(PXWEB_TABLE, json=query, timeout=120)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            await asyncio.sleep(delay)
            delay *= 2
            continue
        logger.error("PxWeb %d: %s", resp.status_code, resp.text[:200])
        return None
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_year", type=int, default=2015)
    parser.add_argument("--to", dest="to_year", type=int, default=date.today().year)
    parser.add_argument("--force-full", action="store_true",
                        help="Refetch all months even if already in DB")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_async_engine(DB_URL, future=True)

    # Skip year-months we already have in DB unless --force-full.
    if args.force_full:
        skip_yms: set[str] = set()
    else:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT DISTINCT period_year_month FROM property.construction_activity"))
            skip_yms = {r[0] for r in result.fetchall()}

    requested = _months(args.from_year, args.to_year)

    # Cap to actually-published months — StatFi has a publishing lag, future
    # months in the requested range would otherwise cause 400 errors.
    published = await _published_months()
    if published is not None:
        before = len(requested)
        requested = [m for m in requested if m in published]
        unpublished_skipped = before - len(requested)
        if unpublished_skipped:
            logger.info("Skipping %d unpublished month(s) outside StatFi's domain",
                        unpublished_skipped)

    months = [m for m in requested
              if f"{m[:4]}-{m[5:].zfill(2)}" not in skip_yms]
    if not months:
        logger.info("All %d requested+published months already cached — nothing to fetch",
                    len(requested))
        await engine.dispose()
        return 0
    logger.info("Fetching %d new months (skipped %d already cached)",
                len(months), len(requested) - len(months))

    # PxWeb 156f has aggressive cell-count limits — narrow to a single phase
    # per request and 12 months per batch (~5760 cells per call).
    batch_size = 12
    phases_to_fetch = ["1", "2", "3"]  # permit, start, completion
    upsert_sql = text(
        """
        INSERT INTO property.construction_activity
            (region_code, period_year_month, period_start, phase_code, phase,
             building_class_code, new_dwellings, floor_area_m2, volume_m3, activity_count, fetched_at)
        VALUES (:region_code, :period_year_month, :period_start, :phase_code, :phase,
                :building_class_code, :new_dwellings, :floor_area_m2, :volume_m3, :activity_count, now())
        ON CONFLICT (region_code, period_year_month, phase_code, building_class_code)
        DO UPDATE SET
            new_dwellings  = COALESCE(EXCLUDED.new_dwellings,  property.construction_activity.new_dwellings),
            floor_area_m2  = COALESCE(EXCLUDED.floor_area_m2,  property.construction_activity.floor_area_m2),
            volume_m3      = COALESCE(EXCLUDED.volume_m3,      property.construction_activity.volume_m3),
            activity_count = COALESCE(EXCLUDED.activity_count, property.construction_activity.activity_count),
            fetched_at     = now()
        """
    )

    total_written = 0
    async with httpx.AsyncClient() as client:
        # Iterate phases × month-batches (smaller payloads to avoid 403s)
        for phase in phases_to_fetch:
          for i in range(0, len(months), batch_size):
            batch = months[i : i + batch_size]
            logger.info("Phase %s — batch %d/%d: %s..%s",
                        phase, i // batch_size + 1,
                        (len(months) + batch_size - 1) // batch_size,
                        batch[0], batch[-1])
            query = {
                "query": [
                    # PxWeb-päivitys 2026-06-08: dimensiokoodit teknisiin id:ihin,
                    # contentscode-arvot "raku-"-etuliitteellä (stripataan parsinnassa).
                    {"code": "rakennusvaihe_1_20250101", "selection": {"filter": "item", "values": [phase]}},
                    {"code": "alue_23_20260101",         "selection": {"filter": "all", "values": ["*"]}},
                    {"code": "timeperiod_m",             "selection": {"filter": "item", "values": batch}},
                    {"code": "rakennus_6_20180101",      "selection": {"filter": "all", "values": ["*"]}},
                    {"code": "contentscode", "selection": {"filter": "item", "values": [
                        "raku-tilavuusToimenpide", "raku-kerrosalaToimenpide", "raku-uusiAsuntoLkm", "raku-rakentamistoimenpideLkm",
                    ]}},
                ],
                "response": {"format": "json-stat2"},
            }
            data = await _fetch(client, query)
            if not data:
                continue
            rows = _decode_jsonstat(data)

            # Group cells with the same dimensions but different ContentCode
            grouped: dict[tuple, dict] = {}
            for r in rows:
                phase = r.get("rakennusvaihe_1_20250101", r.get("rakennusvaihe", ""))
                region = r.get("alue_23_20260101", r.get("alue", ""))
                period = r.get("timeperiod_m", r.get("timeperiod", ""))
                bclass = r.get("rakennus_6_20180101", r.get("rakennusluokitus2018", ""))
                content = r.get("contentscode", r.get("ContentCode", "")).removeprefix("raku-")
                if not (phase and region and period and bclass and content):
                    continue
                key = (region, period, phase, bclass)
                slot = grouped.setdefault(key, {})
                slot[content] = r["value"]

            async with engine.begin() as conn:
                written = 0
                for (region, period, phase, bclass), m in grouped.items():
                    y, mo = period.split("M")
                    period_start = date(int(y), int(mo), 1)
                    await conn.execute(upsert_sql, {
                        "region_code": region,
                        "period_year_month": f"{y}-{mo}",
                        "period_start": period_start,
                        "phase_code": int(phase),
                        "phase": PHASE_LABEL.get(phase, phase),
                        "building_class_code": bclass,
                        "new_dwellings":   int(m["uusiAsuntoLkm"]) if m.get("uusiAsuntoLkm") is not None else None,
                        "floor_area_m2":   m.get("kerrosalaToimenpide"),
                        "volume_m3":       m.get("tilavuusToimenpide"),
                        "activity_count":  int(m["rakentamistoimenpideLkm"]) if m.get("rakentamistoimenpideLkm") is not None else None,
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
