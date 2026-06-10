#!/usr/bin/env python3
"""Fetch StatFi rental rate history (vuokratiedot) into property.rent_snapshot.

Combines two tables:
- statfinpas_asvu_pxt_13eb_2025q4: postal-code level history 2015Q1-2025Q4
- statfin_asvu_pxt_15fa: current quarter(s), 2025Q1+ (city-level only)

Idempotent. Safe to re-run. Default mode runs the historical pull once
plus the most recent quarter's update.

Usage:
    python3 scripts/fetch_statfi_rents.py
    python3 scripts/fetch_statfi_rents.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("property-intel.rents")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

PXWEB_BASE = "https://pxdata.stat.fi/PXWeb/api/v1/fi"
# HUOM: Passiivi-arkisto käyttää yhä VANHAA PxWeb-muotoa (pitkä taulu-id +
# suomenkieliset dimensiokoodit) — aktiivinen StatFin siirtyi 2026-06-08
# lyhyisiin id:ihin ja teknisiin dimensiokoodeihin. Älä "korjaa" tätä.
HISTORICAL_TABLE = "StatFin_Passiivi/asvu/statfinpas_asvu_pxt_13eb_2025q4.px"

# Nykytaulu (2025=100-pohjainen vuokratilasto): VAIN kunta/aluetaso —
# postinumerotason sarja (13eb) päättyi pysyvästi 2025Q4:ään. Rivit
# tallennetaan rent_snapshotiin alue-koodilla postal_code-sarakkeessa
# ('SSS' = koko maa, '091' = Helsinki jne.) ja source-markkerilla, jotta
# kuntataso ei sekoitu postinumerotasoon (eivät osu 5-numeroisiin joineihin).
CURRENT_TABLE = "StatFin/asvu/15fa.px"
CURRENT_SOURCE = "statfi_asvu_15fa"
# Jatkuvuus 13eb:n kanssa: vain vapaarahoitteiset (rahoitus=1).
CURRENT_RAHOITUS = "1"
CURRENT_ROOM_BAND = {"1": "1h", "2": "2h", "3": "3h+"}
# Kuntakoodit (3 numeroa) + koko maa; pois pks/msu/maakunnat/Helsingin osa-alueet
_CURRENT_AREA_RE = re.compile(r"^(\d{3}|SSS)$")

# Map StatFi room_count code → our band label
ROOM_BAND = {"01": "1h", "02": "2h", "03": "3h+"}


def _quarter_to_dates(q: str) -> tuple[date, date]:
    """'2024Q1' → (2024-01-01, 2024-03-31)."""
    y = int(q[:4])
    qn = int(q[-1])
    start = date(y, (qn - 1) * 3 + 1, 1)
    if qn == 4:
        end = date(y, 12, 31)
    else:
        end = date(y, qn * 3 + 1, 1) - timedelta(days=1)
    return start, end


async def _fetch_pxweb(
    client: httpx.AsyncClient, table: str, query: dict, max_retries: int = 4
) -> dict | None:
    """POST a PxWeb query with simple exponential backoff for 429s."""
    url = f"{PXWEB_BASE}/{table}"
    delay = 2.0
    for attempt in range(max_retries):
        resp = await client.post(url, json=query, timeout=120)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            if attempt + 1 < max_retries:
                logger.warning("429 rate-limit, sleeping %.1fs then retrying…", delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
        logger.error("PxWeb POST %s → %d: %s", url, resp.status_code, resp.text[:200])
        return None
    return None


def _decode_jsonstat(data: dict) -> list[dict]:
    """Decode JSON-stat2 response into flat dict rows."""
    dim_ids: list[str] = data.get("id", [])
    sizes: list[int] = data.get("size", [])
    dimensions: dict = data.get("dimension", {})
    values: list = data.get("value", [])

    dim_keys: list[list[str]] = []
    for did in dim_ids:
        d = dimensions.get(did, {})
        cat = d.get("category", {})
        idx = cat.get("index", {})
        sorted_keys = sorted(idx, key=lambda k: idx[k])
        dim_keys.append(sorted_keys)

    out: list[dict] = []
    for flat_idx, val in enumerate(values):
        if val is None:
            continue
        rem = flat_idx
        indices: list[int] = []
        for s in reversed(sizes):
            indices.append(rem % s)
            rem //= s
        indices.reverse()
        row: dict[str, str | float] = {"value": val}
        for i, did in enumerate(dim_ids):
            if indices[i] < len(dim_keys[i]):
                row[did] = dim_keys[i][indices[i]]
        out.append(row)
    return out


def _all_quarters(start_year: int = 2015, end_year: int = 2025) -> list[str]:
    return [f"{y}Q{q}" for y in range(start_year, end_year + 1) for q in (1, 2, 3, 4)]


async def _existing_quarters(engine) -> set[str]:
    """Return the set of 'YYYYQ#' codes already present in rent_snapshot."""
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT DISTINCT EXTRACT(YEAR FROM period_start)::int*10 "
            "+ EXTRACT(QUARTER FROM period_start)::int FROM property.rent_snapshot"
        ))
        return {f"{int(r[0])//10}Q{int(r[0])%10}" for r in result.fetchall()}


async def fetch_historical(client: httpx.AsyncClient, force_full: bool = False) -> list[dict]:
    """Fetch rent history quarter-by-quarter, skipping ones already in DB.

    On first run pulls the full 2015Q1-2024Q4 series (44 quarters).
    On weekly cron reruns pulls only newly-released quarters (typically zero
    or one), keeping the PxWeb 429 quota low.
    """
    engine = create_async_engine(DB_URL, future=True)
    try:
        already = set() if force_full else await _existing_quarters(engine)
    finally:
        await engine.dispose()

    quarters = [q for q in _all_quarters() if q not in already]
    if not quarters:
        logger.info("All %d historical quarters already cached — nothing to fetch", len(_all_quarters()))
        return []
    logger.info("Fetching %d missing quarter-batches (skipped %d already cached)…",
                len(quarters), len(already))
    all_rows: list[dict] = []
    for i, q in enumerate(quarters, 1):
        query = {
            "query": [
                {"code": "Vuosineljännes", "selection": {"filter": "item", "values": [q]}},
                {"code": "Postinumero",     "selection": {"filter": "all",  "values": ["*"]}},
                {"code": "Huoneluku",        "selection": {"filter": "all",  "values": ["*"]}},
                {"code": "Tiedot",           "selection": {"filter": "item", "values": ["lkm_ptno", "keskivuokra"]}},
            ],
            "response": {"format": "json-stat2"},
        }
        data = await _fetch_pxweb(client, HISTORICAL_TABLE, query)
        if not data:
            logger.warning("Quarter %s: fetch failed, skipping", q)
            continue
        rows = _decode_jsonstat(data)
        all_rows.extend(rows)
        if i % 8 == 0 or i == len(quarters):
            logger.info("  %d/%d quarters fetched (%d cells so far)", i, len(quarters), len(all_rows))
        # PxWeb rate-limits aggressive requests — be polite.
        await asyncio.sleep(0.7)
    return all_rows


async def write_rent_rows(rows: list[dict], dry_run: bool) -> int:
    """Group cells (rent + count for same postal/quarter/room) and upsert."""
    if not rows:
        return 0

    grouped: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        q = r.get("Vuosineljännes", "")
        pc = r.get("Postinumero", "")
        rc = r.get("Huoneluku", "")
        measure = r.get("Tiedot", "")
        if not q or not pc or rc not in ROOM_BAND:
            continue
        key = (pc, q, rc)
        slot = grouped.setdefault(key, {})
        if measure == "keskivuokra":
            slot["rent"] = r["value"]
        elif measure == "lkm_ptno":
            slot["count"] = r["value"]

    if dry_run:
        logger.info("DRY: would upsert %d rent_snapshot rows", len(grouped))
        return 0

    engine = create_async_engine(DB_URL, future=True)
    upsert_sql = text(
        """
        INSERT INTO property.rent_snapshot
            (postal_code, period_start, period_end, room_count_band,
             median_rent_per_m2, rental_contract_count, source, fetched_at)
        VALUES (:postal_code, :period_start, :period_end, :room_count_band,
                :rent, :count, 'statfi_asvu', now())
        ON CONFLICT (postal_code, period_start, period_end, room_count_band)
        DO UPDATE SET
            median_rent_per_m2     = EXCLUDED.median_rent_per_m2,
            rental_contract_count  = EXCLUDED.rental_contract_count,
            fetched_at             = EXCLUDED.fetched_at
        """
    )
    written = 0
    async with engine.begin() as conn:
        for (pc, q, rc), slot in grouped.items():
            ps, pe = _quarter_to_dates(q)
            await conn.execute(upsert_sql, {
                "postal_code": pc,
                "period_start": ps,
                "period_end": pe,
                "room_count_band": ROOM_BAND[rc],
                "rent": slot.get("rent"),
                "count": int(slot["count"]) if slot.get("count") else None,
            })
            written += 1
    await engine.dispose()
    return written


async def _existing_current_quarters(engine) -> set[str]:
    """Quarters already fetched from the current (15fa) table."""
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT DISTINCT EXTRACT(YEAR FROM period_start)::int*10 "
            "+ EXTRACT(QUARTER FROM period_start)::int "
            "FROM property.rent_snapshot WHERE source = :src"
        ), {"src": CURRENT_SOURCE})
        return {f"{int(r[0])//10}Q{int(r[0])%10}" for r in result.fetchall()}


async def _current_available_quarters(client: httpx.AsyncClient) -> list[str]:
    """Probe 15fa metadata for published quarters."""
    resp = await client.get(f"{PXWEB_BASE}/{CURRENT_TABLE}", timeout=30)
    resp.raise_for_status()
    for v in resp.json().get("variables", []):
        if v.get("code") == "timeperiod_q":
            return v.get("values", [])
    return []


async def fetch_and_write_current(
    client: httpx.AsyncClient, dry_run: bool, force_full: bool = False
) -> int:
    """Fetch kunta/koko maa -level rent quarters from the 15fa table.

    Returns the number of rent_snapshot rows upserted.
    """
    engine = create_async_engine(DB_URL, future=True)
    try:
        already = set() if force_full else await _existing_current_quarters(engine)
    finally:
        await engine.dispose()

    try:
        available = await _current_available_quarters(client)
    except Exception as e:
        logger.error("Current-lane (15fa): metadata probe failed: %s", e)
        return 0

    quarters = [q for q in available if q not in already]
    if not quarters:
        logger.info("Current-lane (15fa): all %d quarters already cached", len(available))
        return 0
    logger.info("Current-lane (15fa): fetching %d quarter(s): %s", len(quarters), quarters)

    grouped: dict[tuple[str, str, str], dict] = {}
    for q in quarters:
        query = {
            "query": [
                {"code": "timeperiod_q", "selection": {"filter": "item", "values": [q]}},
                {"code": "alue_44_20260101", "selection": {"filter": "all", "values": ["*"]}},
                {"code": "rahoitus_2_20260101", "selection": {"filter": "item", "values": [CURRENT_RAHOITUS]}},
                {"code": "huoneluku_5_20260101", "selection": {"filter": "item", "values": ["1", "2", "3"]}},
                {"code": "contentscode", "selection": {"filter": "item", "values": [
                    "asvu_keskineliovuokra", "asvu_keskineliovuokra_lkm",
                ]}},
            ],
            "response": {"format": "json-stat2"},
        }
        data = await _fetch_pxweb(client, CURRENT_TABLE, query)
        if not data:
            logger.warning("Quarter %s (15fa): fetch failed, skipping", q)
            continue
        for r in _decode_jsonstat(data):
            area = str(r.get("alue_44_20260101", ""))
            rc = str(r.get("huoneluku_5_20260101", ""))
            qq = str(r.get("timeperiod_q", ""))
            if not _CURRENT_AREA_RE.match(area) or rc not in CURRENT_ROOM_BAND or not qq:
                continue
            slot = grouped.setdefault((area, qq, rc), {})
            measure = r.get("contentscode", "")
            if measure == "asvu_keskineliovuokra":
                slot["rent"] = r["value"]
            elif measure == "asvu_keskineliovuokra_lkm":
                slot["count"] = r["value"]
        await asyncio.sleep(0.7)

    if dry_run:
        logger.info("DRY: would upsert %d city-level rent_snapshot rows", len(grouped))
        return 0
    if not grouped:
        return 0

    engine = create_async_engine(DB_URL, future=True)
    upsert_sql = text(
        """
        INSERT INTO property.rent_snapshot
            (postal_code, period_start, period_end, room_count_band,
             median_rent_per_m2, rental_contract_count, source, fetched_at)
        VALUES (:postal_code, :period_start, :period_end, :room_count_band,
                :rent, :count, :source, now())
        ON CONFLICT (postal_code, period_start, period_end, room_count_band)
        DO UPDATE SET
            median_rent_per_m2     = EXCLUDED.median_rent_per_m2,
            rental_contract_count  = EXCLUDED.rental_contract_count,
            source                 = EXCLUDED.source,
            fetched_at             = EXCLUDED.fetched_at
        """
    )
    written = 0
    async with engine.begin() as conn:
        for (area, q, rc), slot in grouped.items():
            ps, pe = _quarter_to_dates(q)
            await conn.execute(upsert_sql, {
                "postal_code": area,
                "period_start": ps,
                "period_end": pe,
                "room_count_band": CURRENT_ROOM_BAND[rc],
                "rent": slot.get("rent"),
                "count": int(slot["count"]) if slot.get("count") else None,
                "source": CURRENT_SOURCE,
            })
            written += 1
    await engine.dispose()
    return written


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-full", action="store_true",
                        help="Refetch all quarters even if already in DB (bypass skip-existing)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async with httpx.AsyncClient() as client:
        rows = await fetch_historical(client, force_full=args.force_full)
        written_hist = await write_rent_rows(rows, args.dry_run) if rows else 0
        written_cur = await fetch_and_write_current(client, args.dry_run, args.force_full)

    logger.info("Done: %d historical + %d current (15fa) rows written", written_hist, written_cur)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
