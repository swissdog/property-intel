#!/usr/bin/env python3
"""Fetch Finnish mortgage-market monthly metrics from ECB SDW into
property.bof_housing_loan_metric.

Bank of Finland reports housing-loan statistics through the ECB
Statistical Data Warehouse (same API used by fetch_interest_rates.py).
Two dataflows are queried:

* **MIR** — Monetary Financial Institutions Interest Rates
            New business + outstanding amount, with rate + margin breakdowns
* **BSI** — Bank Balance Sheet Items
            Outstanding amount of housing loans, monthly volumes

Series codes follow ECB SDW conventions for FI (Finland) reporters.
Each series maps to a stable ``metric_code`` we own:

  metric_code                       | unit | dataflow.series_key
  ----------------------------------+------+-----------------------------------------------------
  avg_rate_new_loans                | pct  | MIR.M.FI.B.A2C.A.R.A.2250.EUR.N
  avg_rate_outstanding              | pct  | MIR.M.FI.B.A22.A.R.A.2250.EUR.O
  new_loans_volume_meur             | meur | MIR.M.FI.B.A2C.A.B.A.2250.EUR.N
  stock_meur                        | meur | BSI.M.FI.N.A.A22.A.1.U6.2250.Z01.E

Codes verified 2026-05-16 against live ECB SDW. The previous 2240.* codes
returned 404 — IR_TYPE 2240 was an older classification, current is 2250.

Idempotent: ON CONFLICT (period, metric_code) DO UPDATE.
Default range: 2015-01-01 → today. Safe to re-run.

Usage:
    python3 scripts/fetch_bof_loans.py
    python3 scripts/fetch_bof_loans.py --from 2020 --to 2026
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

logger = logging.getLogger("property-intel.bof-loans")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

ECB_BASE = "https://data-api.ecb.europa.eu/service/data"

# (metric_code, unit, dataflow, series_key)
# IR_TYPE 2250 = "Lending for house purchase excluding revolving loans and
# overdrafts" — current ECB classification (replaced legacy 2240 ~2020s).
# IR_BUS_COV: N = new business, O = outstanding amount.
LOAN_SERIES: list[tuple[str, str, str, str]] = [
    # New business: AAR/NDER rate on new house-purchase loans
    ("avg_rate_new_loans",        "pct",  "MIR", "M.FI.B.A2C.A.R.A.2250.EUR.N"),
    # Outstanding amount: weighted-average rate on the existing housing-loan book
    ("avg_rate_outstanding",      "pct",  "MIR", "M.FI.B.A22.A.R.A.2250.EUR.O"),
    # New business: monthly gross new business volume (€ million)
    ("new_loans_volume_meur",     "meur", "MIR", "M.FI.B.A2C.A.B.A.2250.EUR.N"),
    # Stock: outstanding loans for house purchase to households (€ million)
    ("stock_meur",                "meur", "BSI", "M.FI.N.A.A22.A.1.U6.2250.Z01.E"),
]


def _period_to_date(period: str) -> date | None:
    """ECB SDW monthly TIME_PERIOD '2024-01' → 2024-01-01."""
    period = period.strip()
    try:
        if len(period) == 7 and period[4] == "-":
            y, m = period.split("-")
            return date(int(y), int(m), 1)
        if len(period) == 10 and period.count("-") == 2:
            y, m, d = period.split("-")
            return date(int(y), int(m), int(d))
    except (ValueError, IndexError):
        return None
    return None


async def _fetch_series(
    client: httpx.AsyncClient, dataflow: str, series_key: str,
    start: str, end: str,
) -> list[tuple[str, float]]:
    """Fetch ECB SDW series as CSV → [(period, value), ...]."""
    url = f"{ECB_BASE}/{dataflow}/{series_key}"
    params = {"format": "csvdata", "startPeriod": start, "endPeriod": end}
    resp = await client.get(url, params=params, timeout=60)
    if resp.status_code == 404:
        logger.warning("Series %s/%s returned 404 (empty)", dataflow, series_key)
        return []
    if resp.status_code != 200:
        logger.error("ECB SDW %s/%s → HTTP %d: %s",
                     dataflow, series_key, resp.status_code, resp.text[:200])
        return []

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
    parser.add_argument("--from", dest="start", default="2015-01-01")
    parser.add_argument("--to",   dest="end",   default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.dry_run:
        logger.info("DRY-RUN: would fetch %d series from %s to %s",
                    len(LOAN_SERIES), args.start, args.end)
        return 0

    engine = create_async_engine(DB_URL, future=True)
    upsert_sql = text(
        """
        INSERT INTO property.bof_housing_loan_metric
            (period, metric_code, value, unit, source_series, source_provider, fetched_at)
        VALUES (:period, :metric_code, :value, :unit, :source_series, 'ECB_BoF', now())
        ON CONFLICT (period, metric_code) DO UPDATE SET
            value          = EXCLUDED.value,
            unit           = EXCLUDED.unit,
            source_series  = EXCLUDED.source_series,
            fetched_at     = EXCLUDED.fetched_at
        """
    )

    total_inserted = 0
    async with httpx.AsyncClient() as client:
        for metric_code, unit, dataflow, series_key in LOAN_SERIES:
            logger.info("Fetching %s ← %s.%s", metric_code, dataflow, series_key)
            try:
                obs = await _fetch_series(client, dataflow, series_key, args.start, args.end)
            except Exception:
                logger.exception("Fetch failed for %s", metric_code)
                continue

            written = 0
            async with engine.begin() as conn:
                for period, value in obs:
                    obs_date = _period_to_date(period)
                    if obs_date is None:
                        continue
                    await conn.execute(upsert_sql, {
                        "period": obs_date,
                        "metric_code": metric_code,
                        "value": value,
                        "unit": unit,
                        "source_series": f"{dataflow}.{series_key}",
                    })
                    written += 1
            total_inserted += written
            logger.info("  → %d observations written", written)

    await engine.dispose()
    logger.info("Done: %d total observations across %d series",
                total_inserted, len(LOAN_SERIES))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
