#!/usr/bin/env python3
"""Fetch mortgage-relevant interest rates from ECB SDW into property.interest_rate.

Fetches:
- Euribor 1M / 3M / 6M / 12M (monthly average, used as Finnish mortgage indices)
- ECB main refinancing operations (MRO) rate (daily, drives all Euribor)
- ECB deposit facility rate (DFR) (daily, marginal)

Idempotent. Default range: 2020-01-01 → today. Safe to re-run hourly:
joka ajolla kaikki Euriborit tallentuvat (ON CONFLICT DO UPDATE), eli
historiarivit eivät katoa ja uudet kuukausi-päätökset päivittyvät.

Usage:
    python3 scripts/fetch_interest_rates.py
    python3 scripts/fetch_interest_rates.py --from 2020 --to 2026
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("property-intel.rates")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

ECB_BASE = "https://data-api.ecb.europa.eu/service/data"

# rate_type, dataflow, series_key, frequency
RATE_SERIES: list[tuple[str, str, str, str]] = [
    ("euribor_1m",   "FM", "M.U2.EUR.RT.MM.EURIBOR1MD_.HSTA",  "M"),
    ("euribor_3m",   "FM", "M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA",  "M"),
    ("euribor_6m",   "FM", "M.U2.EUR.RT.MM.EURIBOR6MD_.HSTA",  "M"),
    ("euribor_12m",  "FM", "M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA",  "M"),
    ("ecb_mro",      "FM", "D.U2.EUR.4F.KR.MRR_FR.LEV",        "D"),
    ("ecb_dfr",      "FM", "D.U2.EUR.4F.KR.DFR.LEV",           "D"),
]


def _period_to_date(period: str, freq: str) -> date | None:
    """Convert TIME_PERIOD field to a calendar date.

    M (monthly): '2024-01' → 2024-01-01
    D (daily):   '2024-01-15' → 2024-01-15
    Q (quarterly): '2024-Q1' → 2024-01-01
    """
    period = period.strip()
    try:
        if freq == "D" or len(period) == 10:
            y, m, d = period.split("-")
            return date(int(y), int(m), int(d))
        if freq == "M" and "-" in period:
            y, m = period.split("-")
            return date(int(y), int(m), 1)
        if freq == "Q" and "Q" in period:
            y, q = period.split("-Q")
            return date(int(y), (int(q) - 1) * 3 + 1, 1)
    except (ValueError, IndexError):
        return None
    return None


async def _fetch_series(
    client: httpx.AsyncClient, dataflow: str, series_key: str,
    start: str, end: str,
) -> list[tuple[str, float]]:
    """Fetch ECB SDW series as CSV. Returns [(period, value), ...]."""
    url = f"{ECB_BASE}/{dataflow}/{series_key}"
    params = {"format": "csvdata", "startPeriod": start, "endPeriod": end}
    resp = await client.get(url, params=params, timeout=60)
    if resp.status_code == 404:
        logger.warning("Series %s/%s returned 404 (empty)", dataflow, series_key)
        return []
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    rows: list[tuple[str, float]] = []
    for row in reader:
        period = row.get("TIME_PERIOD", "")
        raw_value = row.get("OBS_VALUE", "")
        if not period or not raw_value:
            continue
        try:
            v = float(raw_value)
        except ValueError:
            continue
        rows.append((period, v))
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", default="2020-01-01")
    parser.add_argument("--to", dest="end", default=date.today().isoformat())
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    engine = create_async_engine(DB_URL, future=True)
    upsert_sql = text(
        """
        INSERT INTO property.interest_rate
            (rate_type, observation_date, frequency, value_pct,
             source_series, source_provider, fetched_at)
        VALUES (:rate_type, :observation_date, :frequency, :value_pct,
                :source_series, 'ECB', now())
        ON CONFLICT (rate_type, observation_date) DO UPDATE SET
            value_pct  = EXCLUDED.value_pct,
            fetched_at = EXCLUDED.fetched_at
        """
    )

    total_inserted = 0
    async with httpx.AsyncClient() as client:
        for rate_type, dataflow, series_key, freq in RATE_SERIES:
            logger.info("Fetching %s (%s)", rate_type, series_key)
            try:
                obs = await _fetch_series(client, dataflow, series_key, args.start, args.end)
            except Exception:
                logger.exception("Fetch failed for %s", rate_type)
                continue

            written = 0
            async with engine.begin() as conn:
                for period, value in obs:
                    obs_date = _period_to_date(period, freq)
                    if obs_date is None:
                        continue
                    await conn.execute(upsert_sql, {
                        "rate_type": rate_type,
                        "observation_date": obs_date,
                        "frequency": freq,
                        "value_pct": value,
                        "source_series": f"{dataflow}.{series_key}",
                    })
                    written += 1
            total_inserted += written
            logger.info("  → %d observations written", written)

    await engine.dispose()
    logger.info("Done: %d total observations across %d rate types",
                total_inserted, len(RATE_SERIES))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
