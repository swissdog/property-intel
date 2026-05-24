#!/usr/bin/env python3
"""Export property-intel analytical views as a clean CSV bundle.

This is the product output a paying customer would receive — not the raw
operational tables, but the analyst-ready denormalized views joined across
listings, transactions, rents, construction, migration, and rates.

Output structure (in --out directory, default data/exports/<YYYY-MM-DD>/):
    investor_lens.csv          — postal-code latest yield + price growth
    yield_anomalies.csv        — above-median yield + below-median price
    market_velocity.csv        — quarterly DOM/inventory per pc
    supply_demand.csv          — annual supply (completions) vs demand (migration)
    national_headline.csv      — national time-series of price/rent/yield/rate
    interest_rates.csv         — full rate history
    rent_snapshot.csv          — full rent history per pc
    README.md                  — schema + provenance + freshness

Usage:
    python3 scripts/export_product_bundle.py
    python3 scripts/export_product_bundle.py --out /tmp/bundle
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("property-intel.export")

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)

EXPORTS: list[tuple[str, str]] = [
    ("investor_lens",     "SELECT * FROM property.v_postal_investor_lens"),
    ("yield_anomalies",   "SELECT * FROM property.v_yield_anomalies"),
    ("market_velocity",   "SELECT * FROM property.v_market_velocity_timeseries"),
    ("supply_demand",     "SELECT * FROM property.v_supply_demand"),
    ("national_headline", "SELECT * FROM property.v_national_headline"),
    ("interest_rates",    "SELECT rate_type, observation_date, frequency, value_pct, "
                          "source_series FROM property.interest_rate ORDER BY rate_type, observation_date"),
    ("rent_snapshot",     "SELECT postal_code, period_start, room_count_band, "
                          "median_rent_per_m2, rental_contract_count "
                          "FROM property.rent_snapshot ORDER BY postal_code, period_start"),
]


async def export_query(conn, name: str, query: str, out_dir: Path) -> int:
    result = await conn.execute(text(query))
    rows = result.mappings().all()
    out_path = out_dir / f"{name}.csv"
    if not rows:
        out_path.write_text("")
        logger.warning("%s: 0 rows", name)
        return 0
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    logger.info("%s: %d rows → %s", name, len(rows), out_path)
    return len(rows)


def write_readme(out_dir: Path, counts: dict[str, int]) -> None:
    today = date.today().isoformat()
    text_md = f"""# Property-Intel Data Bundle — {today}

Coverage: Finnish residential housing market.
Sources: Oikotie (listings), Hintatiedot.fi (transactions, rolling 12 mo),
Tilastokeskus PxWeb (statfi_ashi, statfi_asvu, statfi_raku, statfi_muutl, paavo),
ECB Statistical Data Warehouse (Euribor, MRO/DFR), Tilastokeskus Paavo (postal-code geometries).

## Files

| File | Rows | Description |
|---|---:|---|
| `investor_lens.csv`     | {counts.get('investor_lens', 0):,} | Latest yield, sold price, rent, 5y growth per postal code |
| `yield_anomalies.csv`   | {counts.get('yield_anomalies', 0):,} | Areas with above-median yield AND below-median price (deal lens) |
| `market_velocity.csv`   | {counts.get('market_velocity', 0):,} | Quarterly listings / DOM / removed-vs-active per postal code |
| `supply_demand.csv`     | {counts.get('supply_demand', 0):,} | Annual: dwellings completed vs net migration (demand/supply ratio) |
| `national_headline.csv` | {counts.get('national_headline', 0):,} | Quarterly Finland-wide avg price, rent, Euribor 12M, yield |
| `interest_rates.csv`    | {counts.get('interest_rates', 0):,} | Full rate series 2020+ (Euribor 1M/3M/6M/12M, ECB MRO/DFR) |
| `rent_snapshot.csv`     | {counts.get('rent_snapshot', 0):,} | Postal-code-level rent history 2015Q1-2024Q2 |

## Data freshness

- Listings (Oikotie):           hourly
- Transactions (Hintatiedot):   hourly (rolling 12 mo only — historical depth not exposed by source)
- Apartment-prices (StatFi):    quarterly (~2-quarter publishing lag)
- Rents (StatFi):                quarterly (~2-quarter publishing lag)
- Construction (StatFi):         monthly
- Migration (StatFi):            yearly
- Interest rates (ECB):          daily

## Methodology notes

- **Yield (gross)**: `(median_rent_per_m2 × 12) / median_sold_per_m2 × 100`. 2-room apartment proxy used (room_count_band = '2h', segment = 'Kerrostalo kaksiot').
- **Demand/supply ratio**: `total_net_migration / new_dwellings_completed × 100`. Ratios above 100% suggest demand outstripping new supply.
- **Postal_code coverage**: 100% of property_assets carry a postal_code, resolved either from source (Hintatiedot transactions) or via PostGIS reverse-geocoding (Oikotie listings, lat/lon → Paavo polygon, with 500 m nearest fallback for shoreline edge cases).
- **5y price growth**: ratio of `median_sold_per_m2` between latest available quarter and quarter ≥5 years prior. Missing where small areas had insufficient transaction volume in the comparison quarter.

## Limitations

- Hintatiedot transaction history is rolling 12 months only — historical price trends rely on StatFi 2020-2025 aggregates, not individual transactions.
- Construction permits are at maakunta (region) level only — drilldown to municipality/postal_code requires kunta-avoindata sources not yet integrated.
- Migration data lags ~12-18 months (StatFi annual release cycle).
- ~22 listings carry NULL postal_code where lat/lon was outside Finnish polygon coverage (offshore islands, recent coastal redevelopment).

## Update cadence

Hourly pipeline runs at minute :17. Detail enrichment (fees, condition, energy class)
runs continuously per pipeline run with budget = 100 listings/hour.
"""
    (out_dir / "README.md").write_text(text_md)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: data/exports/<today>)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.out is None:
        out_dir = Path(__file__).resolve().parent.parent / "data" / "exports" / date.today().isoformat()
    else:
        out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", out_dir)

    engine = create_async_engine(DB_URL, future=True)
    counts: dict[str, int] = {}
    async with engine.connect() as conn:
        for name, query in EXPORTS:
            try:
                counts[name] = await export_query(conn, name, query, out_dir)
            except Exception:
                logger.exception("Export %s failed", name)
                counts[name] = 0
    await engine.dispose()

    write_readme(out_dir, counts)
    total = sum(counts.values())
    logger.info("Bundle complete: %d total rows across %d files", total, len(EXPORTS))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
