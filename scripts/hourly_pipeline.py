#!/usr/bin/env python3
"""Property Intel — Hourly data pipeline.

Fetches data from all enabled sources, writes to the property database,
and refreshes materialized views. Designed to run via cron every hour.

Usage:
    python3 scripts/hourly_pipeline.py
    python3 scripts/hourly_pipeline.py --sources statfi,paavo
    python3 scripts/hourly_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

# Ensure property-intel root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

# Load repo-root .env so scheduled/cron runs reach the REAL DB even when the
# env var isn't exported. setdefault → never overrides an explicitly set var.
# Without this the fallback below (a dev placeholder) silently pointed at the
# wrong port/db and wrote 0 rows.
_ENV_FILE = os.path.join(_ROOT, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)
os.environ["JARVIS_PROPERTY_INTEL_DATABASE_URL"] = DB_URL

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from jarvis_property_intel.models import AreaSnapshot, Base, Listing, ListingEvent, PropertyAsset
from jarvis_property_intel.connectors.base import NormalizedRecord, RawFetchResult
from jarvis_property_intel.connectors.statfi import StatFiConfig, StatFiPxWebConnector
from jarvis_property_intel.connectors.paavo import PaavoConfig, PaavoConnector
from jarvis_property_intel.connectors.oikotie import OikotieConfig, OikotieConnector
from jarvis_property_intel.connectors.hintatiedot import HintatiedotConfig, HintatiedotConnector
from jarvis_property_intel.connectors.mml import MMLConfig, MMLTransactionConnector
from jarvis_property_intel.connectors.mml.ingest import record_to_transaction_params

logger = logging.getLogger("property-intel.pipeline")

# ── Telegram alerting ─────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("JARVIS_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = (
    os.getenv("AUTONOMY_TELEGRAM_CHAT_ID", "").strip()
    or os.getenv("JARVIS_TELEGRAM_ALLOWED_CHATS", "").split(",")[0].strip()
)


def send_telegram_alert(message: str) -> bool:
    """Send an alert message via Telegram Bot API.

    Returns True if sent successfully, False otherwise.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram alerting not configured (missing token or chat_id)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    try:
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                logger.info("Telegram alert sent successfully")
                return True
            logger.error("Telegram API returned HTTP %d", resp.status)
            return False
    except (URLError, OSError) as e:
        logger.error("Failed to send Telegram alert: %s", e)
        return False


# Helsinki-area postal codes for regular fetching
HELSINKI_POSTAL_CODES = [
    "00100", "00120", "00130", "00140", "00150", "00160", "00170", "00180",
    "00200", "00210", "00250", "00260", "00270", "00280", "00300", "00320",
    "00330", "00340", "00350", "00360", "00370", "00380", "00390", "00400",
    "00410", "00420", "00430", "00440", "00500", "00510", "00520", "00530",
    "00540", "00550", "00560", "00570", "00580", "00600", "00610", "00620",
    "00630", "00640", "00650", "00660", "00670", "00680", "00690", "00700",
    "00710", "00720", "00730", "00740", "00750", "00760", "00770", "00780",
    "00790", "00800", "00810", "00820", "00830", "00840", "00850", "00860",
    "00870", "00880", "00890", "00900", "00910", "00920", "00930", "00940",
    "00950", "00960", "00970", "00980", "00990",
    # Espoo
    "02100", "02110", "02120", "02130", "02140", "02150", "02160", "02170",
    "02180", "02200", "02210", "02230", "02240", "02260", "02270", "02280",
    "02320", "02330", "02340", "02360", "02380", "02600", "02610", "02620",
    "02630", "02650", "02660", "02680", "02710", "02720", "02730", "02740",
    "02750", "02760", "02770", "02780",
    # Vantaa
    "01200", "01230", "01260", "01280", "01300", "01340", "01350", "01360",
    "01370", "01380", "01390", "01400", "01420", "01450", "01480", "01490",
    "01510", "01520", "01600", "01610", "01620", "01630", "01640", "01650",
    "01660", "01670", "01680", "01690", "01700", "01710", "01720", "01730",
    "01740", "01750", "01760", "01770",
]

# Latest N quarters to fetch from StatFi
STATFI_QUARTERS_BACK = 4


def _recent_quarters(n: int = STATFI_QUARTERS_BACK) -> list[str]:
    """Return the last N quarter codes that are likely to have data.

    StatFi publishes housing data with ~2–3 quarter lag. For Q2 2026 (Apr),
    the latest available data is typically Q4 2025 or Q3 2025.
    We go back 3 quarters from the current quarter to be safe.
    """
    today = date.today()
    y, q = today.year, (today.month - 1) // 3 + 1

    # Go back 3 quarters from current to account for publication lag
    for _ in range(3):
        q -= 1
        if q == 0:
            q = 4
            y -= 1

    quarters = []
    for _ in range(n):
        quarters.append(f"{y}Q{q}")
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return list(reversed(quarters))


async def fetch_statfi(dry_run: bool = False) -> list[NormalizedRecord]:
    """Fetch apartment price data from Tilastokeskus PxWeb."""
    logger.info("StatFi: starting fetch")
    connector = StatFiPxWebConnector(StatFiConfig())
    try:
        healthy = await connector.health_check()
        if not healthy:
            logger.warning("StatFi: health check failed, skipping")
            return []
    except Exception as e:
        logger.error("StatFi: health check error: %s", e)
        return []

    quarters = _recent_quarters()
    logger.info("StatFi: fetching quarters %s", quarters)

    try:
        results = await connector.fetch_dataset(
            dataset_id="apartment_prices",
            query={
                "query": [
                    {"code": "Vuosineljännes", "selection": {"filter": "item", "values": quarters}},
                    {"code": "Talotyyppi", "selection": {"filter": "item", "values": ["1", "2", "3", "5"]}},
                ],
                "response": {"format": "json-stat2"},
            },
        )
    except Exception as e:
        logger.error("StatFi: fetch failed: %s", e)
        return []

    all_records: list[NormalizedRecord] = []
    for raw in results:
        records = connector.normalize(raw)
        all_records.extend(records)

    logger.info("StatFi: normalized %d records", len(all_records))
    await connector.close()
    return all_records


async def fetch_paavo(dry_run: bool = False) -> list[NormalizedRecord]:
    """Fetch postal code demographics from Paavo WFS."""
    logger.info("Paavo: starting fetch for %d postal codes", len(HELSINKI_POSTAL_CODES))
    connector = PaavoConnector(PaavoConfig())
    try:
        healthy = await connector.health_check()
        if not healthy:
            logger.warning("Paavo: health check failed, skipping")
            return []
    except Exception as e:
        logger.error("Paavo: health check error: %s", e)
        return []

    # Fetch in batches (WFS can handle many but let's be reasonable)
    all_records: list[NormalizedRecord] = []
    batch_size = 50
    for i in range(0, len(HELSINKI_POSTAL_CODES), batch_size):
        batch = HELSINKI_POSTAL_CODES[i:i + batch_size]
        try:
            results = await connector.fetch_dataset(postal_codes=batch)
            for raw in results:
                records = connector.normalize(raw)
                all_records.extend(records)
        except Exception as e:
            logger.error("Paavo: fetch failed for batch %d: %s", i, e)

    logger.info("Paavo: normalized %d records", len(all_records))
    await connector.close()
    return all_records


async def write_statfi_to_db(
    session: AsyncSession, records: list[NormalizedRecord]
) -> int:
    """Write StatFi records to area_snapshot table. Returns count written."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    groups: dict[tuple, dict] = {}
    for record in records:
        if record.record_type != "area_stats":
            continue
        d = record.data
        quarter_str = d.get("Vuosineljännes", "")
        postal_code = d.get("Postinumero", "00000")
        building_type = d.get("Talotyyppi_label", d.get("Talotyyppi", ""))
        measure = d.get("Tiedot", "")
        value = d.get("value")

        if not quarter_str or "Q" not in quarter_str:
            continue

        key = (postal_code, quarter_str, building_type)
        if key not in groups:
            groups[key] = {}
        if measure == "keskihinta_aritm_nw":
            groups[key]["price_m2"] = value
        elif measure == "lkm_julk20":
            groups[key]["count"] = value

    written = 0
    for (postal_code, quarter_str, building_type), measures in groups.items():
        year = int(quarter_str[:4])
        q = int(quarter_str[-1])
        month_start = (q - 1) * 3 + 1
        month_end = q * 3
        period_start = date(year, month_start, 1)
        if month_end == 12:
            period_end = date(year, 12, 31)
        else:
            period_end = date(year, month_end + 1, 1) - timedelta(days=1)

        values = {
            "postal_code": postal_code,
            "municipality": "",
            "period_start": period_start,
            "period_end": period_end,
            "segment": building_type or None,
            "median_sold_m2": measures.get("price_m2"),
            "inventory_count": measures.get("count"),
        }

        stmt = pg_insert(AreaSnapshot).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_area_snapshot_period",
            set_={
                "median_sold_m2": values["median_sold_m2"],
                "inventory_count": values["inventory_count"],
            },
        )
        await session.execute(stmt)
        written += 1

    return written


async def write_paavo_to_db(
    session: AsyncSession, records: list[NormalizedRecord]
) -> int:
    """Write Paavo records to area_snapshot table. Returns count written."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    written = 0
    year = date.today().year
    for record in records:
        if record.record_type != "area_stats":
            continue
        d = record.data
        postal_code = d.get("postal_code", "")
        if not postal_code:
            continue

        values = {
            "postal_code": postal_code,
            "municipality": d.get("municipality_name", d.get("name", "")),
            "period_start": date(year, 1, 1),
            "period_end": date(year, 12, 31),
            "segment": None,
            "income_median": d.get("median_income"),
            "owner_occupancy_ratio": None,
        }

        stmt = pg_insert(AreaSnapshot).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_area_snapshot_period",
            set_={
                "municipality": values["municipality"],
                "income_median": values["income_median"],
            },
        )
        await session.execute(stmt)
        written += 1

    return written


async def fetch_oikotie(dry_run: bool = False) -> tuple[list[NormalizedRecord], set[str]]:
    """Fetch active listings from Oikotie for Helsinki/Espoo/Vantaa."""
    logger.info("Oikotie: starting fetch")
    config = OikotieConfig(
        max_pages=int(os.getenv("OIKOTIE_MAX_PAGES", "10")),
        request_delay=1.5,
    )
    connector = OikotieConnector(config)

    try:
        healthy = await connector.health_check()
        if not healthy:
            logger.warning("Oikotie: health check failed (token acquisition), skipping")
            return []
    except Exception as e:
        logger.error("Oikotie: health check error: %s", e)
        return []

    all_records: list[NormalizedRecord] = []

    # Fetch all tracked cities (Tier 1-3)
    from jarvis_property_intel.connectors.oikotie.connector import LOCATION_IDS
    cities = list(LOCATION_IDS.keys())
    fetched_cities: set[str] = set()

    for city in cities:
        loc = LOCATION_IDS.get(city)
        if not loc:
            continue
        locations = json.dumps([loc])
        try:
            results = await connector.fetch_listings(
                locations=locations,
                max_pages=config.max_pages,
            )
            for raw in results:
                records = connector.normalize(raw)
                all_records.extend(records)
            fetched_cities.add(city)
            logger.info("Oikotie: %s → %d pages", city, len(results))
        except Exception as e:
            logger.error("Oikotie: fetch failed for %s: %s", city, e)

    logger.info("Oikotie: normalized %d listings total (%d/%d cities ok)", len(all_records), len(fetched_cities), len(cities))
    await connector.close()
    return all_records, fetched_cities


async def write_oikotie_to_db(
    session: AsyncSession, records: list[NormalizedRecord],
    fetched_cities: set[str] | None = None,
) -> dict[str, int]:
    """Write Oikotie listings to DB with change tracking.

    For each listing:
    - New → create PropertyAsset + Listing + "created" event
    - Price changed → update Listing + "price_change" event
    - Still active → update last_seen_at
    - Previously active but missing from fetch → mark "removed" (done separately)

    fetched_cities: if provided, only mark listings as stale if their city
    was successfully fetched. Prevents false removals on partial API failures.

    Returns dict with counts: new, updated, price_changes.
    """
    stats = {"new": 0, "updated": 0, "price_changes": 0}
    now = datetime.now(timezone.utc)

    for record in records:
        if record.record_type != "listing":
            continue
        d = record.data
        oikotie_id = str(d.get("oikotie_id", ""))
        if not oikotie_id:
            continue

        try:
            # Check if listing already exists
            result = await session.execute(
                select(Listing).where(
                    Listing.source == "oikotie",
                    Listing.source_listing_id == oikotie_id,
                )
            )
            existing = result.scalar_one_or_none()

            new_price = d.get("asking_price")
            published = d.get("published")
            first_seen = now
            if published:
                try:
                    first_seen = datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            if existing is None:
                # New listing — create asset + listing + event
                asset = PropertyAsset(
                    asset_type=d.get("asset_type", "unknown"),
                    canonical_address=d.get("address", ""),
                    postal_code=d.get("postal_code", ""),
                    municipality=d.get("municipality", d.get("city", "")),
                    lat=d.get("lat"),
                    lon=d.get("lon"),
                    housing_company_name=d.get("housing_company_name"),
                    source_confidence=0.7,
                )
                session.add(asset)
                await session.flush()

                listing = Listing(
                    asset_id=asset.asset_id,
                    source="oikotie",
                    source_listing_id=oikotie_id,
                    first_seen_at=first_seen,
                    last_seen_at=now,
                    status="active",
                    asking_price=new_price,
                    living_area_m2=d.get("living_area_m2"),
                    year_built=d.get("year_built"),
                    rooms=d.get("rooms"),
                    description_text=d.get("description"),
                    energy_class=d.get("energy_class"),
                    json_blob={
                        "district": d.get("district"),
                        "room_configuration": d.get("room_configuration"),
                        "building_type": d.get("building_type"),
                        "floor": d.get("floor"),
                        "floor_count": d.get("floor_count"),
                        "url": d.get("url"),
                        "agency_name": d.get("agency_name"),
                        "new_development": d.get("new_development"),
                    },
                )
                session.add(listing)
                await session.flush()

                event = ListingEvent(
                    listing_id=listing.listing_id,
                    event_type="created",
                    event_at=first_seen,
                    new_value=str(new_price) if new_price else None,
                )
                session.add(event)
                stats["new"] += 1

            else:
                # Existing listing — update last_seen, check price change
                existing.last_seen_at = now
                existing.status = "active"

                # Refresh json_blob so newly-mapped fields (new_development) backfill
                blob = dict(existing.json_blob or {})
                blob["new_development"] = d.get("new_development")
                if d.get("agency_name") is not None:
                    blob["agency_name"] = d.get("agency_name")
                existing.json_blob = blob

                if (
                    new_price is not None
                    and existing.asking_price is not None
                    and abs(new_price - existing.asking_price) > 0.01
                ):
                    # Price changed!
                    old_price = existing.asking_price
                    existing.asking_price = new_price

                    event = ListingEvent(
                        listing_id=existing.listing_id,
                        event_type="price_change",
                        event_at=now,
                        old_value=str(old_price),
                        new_value=str(new_price),
                    )
                    session.add(event)
                    stats["price_changes"] += 1
                    logger.debug(
                        "Price change: %s %s → %s",
                        oikotie_id, old_price, new_price,
                    )

                stats["updated"] += 1

        except Exception:
            logger.exception("Failed to process Oikotie listing %s", oikotie_id)

    # Mark stale listings as removed
    # (listings from oikotie not seen in last 48 hours)
    # Only for cities that were successfully fetched this run
    stale_cutoff = now - timedelta(hours=48)
    stale_conditions = [
        Listing.source == "oikotie",
        Listing.status == "active",
        Listing.last_seen_at < stale_cutoff,
    ]
    if fetched_cities:
        stale_conditions.append(
            Listing.asset.has(PropertyAsset.municipality.in_(fetched_cities))
        )
    stale_result = await session.execute(
        select(Listing).where(*stale_conditions)
    )
    stale_listings = stale_result.scalars().all()
    for listing in stale_listings:
        listing.status = "removed"
        event = ListingEvent(
            listing_id=listing.listing_id,
            event_type="removed",
            event_at=now,
            old_value=str(listing.asking_price) if listing.asking_price else None,
        )
        session.add(event)
    if stale_listings:
        stats["removed"] = len(stale_listings)
        logger.info("Marked %d stale listings as removed", len(stale_listings))

    return stats


async def write_hintatiedot_to_db(
    session: AsyncSession, records: list[NormalizedRecord], run_id: str = ""
) -> dict[str, int]:
    """Write hintatiedot.fi transactions to the transaction table.

    Uses upsert on (source, source_record_id). When updating an existing
    record, logs changes to transaction_history for audit trail.

    Returns {"written": N, "new": N, "updated": N, "changed": N}.
    """
    now = datetime.now(timezone.utc)

    # Check if history table exists (graceful fallback if migration not yet run)
    has_history = False
    try:
        await session.execute(text("SELECT 1 FROM property.transaction_history LIMIT 0"))
        has_history = True
    except Exception:
        pass

    # Fetch existing records for change detection
    existing_map: dict[str, dict] = {}
    try:
        existing = await session.execute(text(
            "SELECT source_record_id, transaction_id, transaction_price, living_area_m2, price_per_m2 "
            "FROM property.transaction WHERE source = 'hintatiedot_kvkl'"
        ))
        for row in existing.fetchall():
            existing_map[row[0]] = {
                "transaction_id": row[1],
                "transaction_price": row[2],
                "living_area_m2": row[3],
                "price_per_m2": row[4],
            }
    except Exception:
        logger.debug("Could not prefetch existing transactions")

    insert_sql = text("""
        INSERT INTO property.transaction
            (transaction_id, source, source_record_id, transaction_date,
             sale_date, sale_date_precision,
             transaction_price, transaction_type, municipality, neighborhood,
             building_type, living_area_m2, price_per_m2, year_built,
             room_config, floor, elevator, condition, lot_type,
             energy_class, fetched_at, first_seen_at)
        VALUES
            (:transaction_id, :source, :source_record_id, :transaction_date,
             :sale_date, :sale_date_precision,
             :transaction_price, :transaction_type, :municipality, :neighborhood,
             :building_type, :living_area_m2, :price_per_m2, :year_built,
             :room_config, :floor, :elevator, :condition, :lot_type,
             :energy_class, :fetched_at, :first_seen_at)
        ON CONFLICT (source, source_record_id)
        DO UPDATE SET
            transaction_price = EXCLUDED.transaction_price,
            living_area_m2 = EXCLUDED.living_area_m2,
            price_per_m2 = EXCLUDED.price_per_m2,
            fetched_at = EXCLUDED.fetched_at
    """)

    history_sql = text("""
        INSERT INTO property.transaction_history
            (transaction_id, source_record_id, field, old_value, new_value, changed_at, run_id)
        VALUES (:txn_id, :src_id, :field, :old_val, :new_val, :changed_at, :run_id)
    """)

    stats = {"written": 0, "new": 0, "updated": 0, "changed": 0}

    for record in records:
        if record.record_type != "transaction":
            continue
        d = record.data
        price = d.get("debt_free_price")
        if not price or price <= 0:
            continue

        src_id = record.source_record_id
        is_new = src_id not in existing_map
        params = {
            "transaction_id": str(uuid.uuid4()),
            "source": "hintatiedot_kvkl",
            "source_record_id": src_id,
            # transaction_date is a legacy NOT NULL column; KVKL gives us no real
            # sale date, so we record the ingest date here for backward compat and
            # flag the truth via sale_date (NULL) + sale_date_precision ('unknown').
            "transaction_date": now.date() if is_new else None,
            "sale_date": None,
            "sale_date_precision": "unknown",
            "transaction_price": price,
            "transaction_type": "sale",
            "municipality": d.get("city", ""),
            "neighborhood": d.get("neighborhood", ""),
            "building_type": d.get("building_type", ""),
            "living_area_m2": d.get("living_area_m2"),
            "price_per_m2": d.get("price_per_m2"),
            "year_built": d.get("year_built"),
            "room_config": d.get("room_config", ""),
            "floor": d.get("floor", ""),
            "elevator": d.get("elevator"),
            "condition": d.get("condition", ""),
            "lot_type": d.get("lot_type", ""),
            "energy_class": d.get("energy_class", ""),
            "fetched_at": now,
            "first_seen_at": now if is_new else None,
        }
        # Don't overwrite transaction_date for existing records
        if not is_new:
            params.pop("transaction_date")
            params.pop("first_seen_at")

        try:
            # Log changes to history BEFORE upsert
            if not is_new and has_history:
                old = existing_map[src_id]
                txn_id = old["transaction_id"]
                for field_name, old_val, new_val in [
                    ("transaction_price", old.get("transaction_price"), price),
                    ("living_area_m2", old.get("living_area_m2"), d.get("living_area_m2")),
                    ("price_per_m2", old.get("price_per_m2"), d.get("price_per_m2")),
                ]:
                    if str(old_val) != str(new_val) and old_val is not None:
                        await session.execute(history_sql, {
                            "txn_id": txn_id,
                            "src_id": src_id,
                            "field": field_name,
                            "old_val": str(old_val),
                            "new_val": str(new_val),
                            "changed_at": now,
                            "run_id": run_id,
                        })
                        stats["changed"] += 1

            # Use separate SQL for new vs update to preserve transaction_date
            if is_new:
                await session.execute(insert_sql, params)
                stats["new"] += 1
            else:
                await session.execute(text("""
                    UPDATE property.transaction SET
                        transaction_price = :transaction_price,
                        living_area_m2 = :living_area_m2,
                        price_per_m2 = :price_per_m2,
                        fetched_at = :fetched_at
                    WHERE source = :source AND source_record_id = :source_record_id
                """), {
                    "transaction_price": price,
                    "living_area_m2": d.get("living_area_m2"),
                    "price_per_m2": d.get("price_per_m2"),
                    "fetched_at": now,
                    "source": "hintatiedot_kvkl",
                    "source_record_id": src_id,
                })
                stats["updated"] += 1

            stats["written"] += 1
        except Exception:
            logger.debug("Upsert failed: %s", record.source_record_id, exc_info=True)

    return stats


# ── MML kauppahintarekisteri (real property deed dates, exact precision) ──────
MML_FETCH_DAYS = int(os.getenv("MML_FETCH_DAYS", "30"))


def _mml_configured() -> bool:
    """MML is wired into the pipeline but inert until an API key is provided.

    The kauppahintarekisteri OGC API (kevät 2026) needs an API key plus a
    confirmed endpoint. Without MML_API_KEY we skip MML cleanly so the hourly
    pipeline never fails on an unconfigured source. See docs/transaction-dates-
    and-eu-sources.md Vaihe 1 kohta 1.
    """
    return bool(os.getenv("MML_API_KEY", "").strip())


async def fetch_mml(dry_run: bool = False) -> list[NormalizedRecord]:
    """Fetch recent MML kauppahintarekisteri transactions (rolling date window).

    Returns [] (logged, not raised) when MML is unconfigured or unreachable, so
    the pipeline degrades gracefully instead of failing.
    """
    if not _mml_configured():
        logger.info("MML not configured (no MML_API_KEY) — skipping MML fetch")
        return []

    connector = MMLTransactionConnector(MMLConfig())
    try:
        if not await connector.health_check():
            logger.warning("MML health-check failed — skipping MML fetch")
            return []
        date_to = date.today()
        date_from = date_to - timedelta(days=MML_FETCH_DAYS)
        raws = await connector.fetch_transactions(date_from=date_from, date_to=date_to)
        records: list[NormalizedRecord] = []
        for raw in raws:
            records.extend(connector.normalize(raw))
        records = [r for r in records if r.record_type == "transaction"]
        logger.info(
            "MML: fetched %d transaction record(s) over last %d days",
            len(records), MML_FETCH_DAYS,
        )
        return records
    except Exception:
        logger.exception("MML fetch failed")
        return []
    finally:
        await connector.close()


async def write_mml_to_db(
    session: AsyncSession, records: list[NormalizedRecord], run_id: str = ""
) -> dict[str, int]:
    """Write MML transactions to the transaction table with exact sale dates.

    Upsert on (source, source_record_id). Links each transaction to an existing
    property_asset by parcel_id (kiinteistötunnus) when one exists; otherwise the
    transaction is recorded unlinked (asset_id NULL) and can be matched later.
    Returns {"written": N, "new": N, "matched": N}.
    """
    now = datetime.now(timezone.utc)

    # Prefetch existing MML record ids to distinguish new vs updated.
    existing: set[str] = set()
    try:
        res = await session.execute(text(
            "SELECT source_record_id FROM property.transaction WHERE source = 'mml_transactions'"
        ))
        existing = {row[0] for row in res.fetchall()}
    except Exception:
        logger.debug("Could not prefetch existing MML transactions")

    insert_sql = text("""
        INSERT INTO property.transaction
            (transaction_id, asset_id, source, source_record_id,
             transaction_date, sale_date, sale_date_precision,
             transaction_price, transaction_type, parcel_id, municipality,
             price_per_m2, fetched_at, first_seen_at)
        VALUES
            (:transaction_id, :asset_id, :source, :source_record_id,
             :transaction_date, :sale_date, :sale_date_precision,
             :transaction_price, :transaction_type, :parcel_id, :municipality,
             :price_per_m2, :fetched_at, :first_seen_at)
        ON CONFLICT (source, source_record_id) DO UPDATE SET
            transaction_price = EXCLUDED.transaction_price,
            sale_date = EXCLUDED.sale_date,
            sale_date_precision = EXCLUDED.sale_date_precision,
            asset_id = COALESCE(EXCLUDED.asset_id, property.transaction.asset_id),
            price_per_m2 = EXCLUDED.price_per_m2,
            fetched_at = EXCLUDED.fetched_at
    """)

    stats = {"written": 0, "new": 0, "matched": 0}
    for record in records:
        params = record_to_transaction_params(record, now)
        if params is None:
            continue

        # Asset matching by parcel_id (kiinteistötunnus). MML covers kiinteistöt;
        # apartment assets from Oikotie rarely carry a parcel_id, so unmatched
        # transactions are expected early and are still recorded with the date.
        asset_id = None
        if params["parcel_id"]:
            res = await session.execute(
                text("SELECT asset_id FROM property.property_asset "
                     "WHERE parcel_id = :pid LIMIT 1"),
                {"pid": params["parcel_id"]},
            )
            row = res.first()
            if row:
                asset_id = row[0]
                stats["matched"] += 1
        params["asset_id"] = asset_id
        params["first_seen_at"] = now

        try:
            await session.execute(insert_sql, params)
            stats["written"] += 1
            if params["source_record_id"] not in existing:
                stats["new"] += 1
        except Exception:
            logger.debug("MML upsert failed: %s", record.source_record_id, exc_info=True)

    return stats


async def fill_missing_postal_codes(session: AsyncSession) -> int:
    """Reverse-geocode lat/lon → postal_code for any property_asset missing it.

    Oikotie /api/cards does not include postal_code; we resolve it via the
    property.postal_code_area polygons (Tilastokeskus Paavo). Idempotent.
    """
    result = await session.execute(text("""
        UPDATE property.property_asset pa
        SET postal_code = property.lookup_postal_code(pa.lat, pa.lon)
        WHERE (pa.postal_code IS NULL OR pa.postal_code = '')
          AND pa.lat IS NOT NULL
          AND pa.lon IS NOT NULL
          AND property.lookup_postal_code(pa.lat, pa.lon) IS NOT NULL
    """))
    return result.rowcount or 0


async def enrich_oikotie_details(
    session: AsyncSession, max_records: int | None = 100
) -> dict[str, int]:
    """Fetch /api/card/{id} for active oikotie listings missing detail enrichment.

    Populates per-listing fee, condition, heating, energy, lift/sauna, lot
    ownership fields. Selects only rows where detail_fetched_at IS NULL,
    so this is idempotent and resumable. Rate-limited by the connector's
    own request_delay.

    Returns counts: fetched, updated, fetch_failed.
    """
    stats = {"fetched": 0, "updated": 0, "fetch_failed": 0}

    query = text(
        """
        SELECT listing_id::text AS lid, source_listing_id
        FROM property.listing
        WHERE source = 'oikotie'
          AND status = 'active'
          AND detail_fetched_at IS NULL
        ORDER BY first_seen_at DESC NULLS LAST
        """
        + ("" if max_records is None else " LIMIT :limit")
    )
    params = {} if max_records is None else {"limit": max_records}
    result = await session.execute(query, params)
    rows = result.mappings().all()
    if not rows:
        return stats

    connector = OikotieConnector(OikotieConfig())
    update_sql = text(
        """
        UPDATE property.listing SET
            maintenance_fee_eur      = :maintenance_fee_eur,
            financial_fee_eur        = :financial_fee_eur,
            water_fee_eur            = :water_fee_eur,
            parking_fee_eur          = :parking_fee_eur,
            sauna_fee_eur            = :sauna_fee_eur,
            share_of_liabilities_eur = :share_of_liabilities_eur,
            debt_free_price          = :debt_free_price,
            apartment_condition_code = :apartment_condition_code,
            heating_method           = :heating_method,
            heating_method_code      = :heating_method_code,
            building_material        = :building_material,
            has_lift                 = :has_lift,
            has_sauna                = :has_sauna,
            lot_ownership_code       = :lot_ownership_code,
            energy_class             = COALESCE(:energy_class_full, energy_class),
            detail_fetched_at        = now()
        WHERE listing_id = :lid
        """
    )

    try:
        for r in rows:
            lid = r["lid"]
            card_id = r["source_listing_id"]
            try:
                detail_raw = await connector.fetch_detail(int(card_id))
            except Exception:
                logger.exception("Detail fetch raised for %s", card_id)
                detail_raw = None
            if not detail_raw:
                stats["fetch_failed"] += 1
                continue
            try:
                normalized = connector.normalize(detail_raw)
            except Exception:
                logger.exception("Normalize failed for %s", card_id)
                stats["fetch_failed"] += 1
                continue
            if not normalized:
                stats["fetch_failed"] += 1
                continue
            stats["fetched"] += 1
            d = normalized[0].data

            # Per-row savepoint so a single bad row (e.g. unexpected string in a
            # numeric field) doesn't poison the whole batch transaction.
            try:
                async with session.begin_nested():
                    await session.execute(update_sql, {
                        "lid": lid,
                        "maintenance_fee_eur":      d.get("maintenance_fee_eur"),
                        "financial_fee_eur":        d.get("financial_fee_eur"),
                        "water_fee_eur":            d.get("water_fee_eur"),
                        "parking_fee_eur":          d.get("parking_fee_eur"),
                        "sauna_fee_eur":            d.get("sauna_fee_eur"),
                        "share_of_liabilities_eur": d.get("share_of_liabilities_eur"),
                        "debt_free_price":          d.get("debt_free_price"),
                        "apartment_condition_code": d.get("apartment_condition_code"),
                        "heating_method":           d.get("heating_method"),
                        "heating_method_code":      d.get("heating_method_code"),
                        "building_material":        d.get("building_material"),
                        "has_lift":                 d.get("has_lift"),
                        "has_sauna":                d.get("has_sauna"),
                        "lot_ownership_code":       d.get("lot_ownership_code"),
                        "energy_class_full":        d.get("energy_class_full"),
                    })
                stats["updated"] += 1
            except Exception:
                logger.exception("DB update failed for listing %s (card %s)", lid, card_id)
                stats["fetch_failed"] += 1
    finally:
        await connector.close()

    return stats


async def refresh_gold_views(engine: Any) -> None:
    """Refresh materialized views."""
    views = [
        "property.latest_listing_state",
        "property.price_change_history",
        "property.market_velocity_by_postal_code",
        "property.price_gap_by_municipality",
    ]
    for view in views:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
            logger.info("Refreshed %s (concurrent)", view)
        except Exception:
            try:
                async with engine.begin() as conn:
                    await conn.execute(text(f"REFRESH MATERIALIZED VIEW {view}"))
                logger.info("Refreshed %s (non-concurrent)", view)
            except Exception as e:
                logger.error("Failed to refresh %s: %s", view, e)


async def run_pipeline(sources: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run the full hourly pipeline."""
    t0 = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())[:8]
    results: dict[str, Any] = {"started_at": t0.isoformat(), "dry_run": dry_run, "run_id": run_id}
    problems: list[str] = []

    enabled_sources = sources or ["statfi", "paavo", "oikotie", "hintatiedot"]
    # MML is opt-in: auto-included in the default run only when configured (API
    # key present). An explicit --sources mml still forces an attempt, which
    # logs and no-ops cleanly if unconfigured.
    if sources is None and _mml_configured() and "mml" not in enabled_sources:
        enabled_sources.append("mml")

    # Log pipeline start. Also sweep stale 'running' rows from prior crashed
    # runs (the cron triggers hourly; nothing healthy stays running >2h).
    try:
        _track_engine = create_async_engine(DB_URL, echo=False)
        _track_sf = async_sessionmaker(_track_engine, class_=AsyncSession, expire_on_commit=False)
        async with _track_sf() as session:
            stale_result = await session.execute(text("""
                UPDATE property.pipeline_run
                SET status = 'failed',
                    completed_at = COALESCE(completed_at, now())
                WHERE status = 'running'
                  AND started_at < now() - INTERVAL '2 hours'
                RETURNING run_id
            """))
            stale_ids = [row[0] for row in stale_result.fetchall()]
            if stale_ids:
                logger.warning(
                    "Marked %d stale running pipeline_run(s) as failed: %s",
                    len(stale_ids), stale_ids,
                )

            await session.execute(text("""
                INSERT INTO property.pipeline_run (run_id, started_at, status, sources_json)
                VALUES (:run_id, :started_at, 'running', :sources)
            """), {"run_id": run_id, "started_at": t0, "sources": json.dumps(enabled_sources)})
            await session.commit()
        await _track_engine.dispose()
    except Exception:
        pass  # Non-critical

    # Fetch from all enabled sources concurrently
    # (Oikotie runs separately since it's slower and needs sequential token flow)
    tasks = {}
    if "statfi" in enabled_sources:
        tasks["statfi"] = asyncio.create_task(fetch_statfi(dry_run))
    if "paavo" in enabled_sources:
        tasks["paavo"] = asyncio.create_task(fetch_paavo(dry_run))
    if "mml" in enabled_sources:
        tasks["mml"] = asyncio.create_task(fetch_mml(dry_run))

    statfi_records: list[NormalizedRecord] = []
    paavo_records: list[NormalizedRecord] = []
    oikotie_records: list[NormalizedRecord] = []
    mml_records: list[NormalizedRecord] = []

    if "statfi" in tasks:
        statfi_records = await tasks["statfi"]
        results["statfi_fetched"] = len(statfi_records)
        if not statfi_records:
            problems.append("StatFi returned 0 records")
    if "paavo" in tasks:
        paavo_records = await tasks["paavo"]
        results["paavo_fetched"] = len(paavo_records)
        if not paavo_records:
            problems.append("Paavo returned 0 records")
    if "mml" in tasks:
        mml_records = await tasks["mml"]
        results["mml_fetched"] = len(mml_records)

    # Oikotie runs after stats sources (rate-limited, sequential)
    oikotie_fetched_cities: set[str] = set()
    if "oikotie" in enabled_sources:
        oikotie_records, oikotie_fetched_cities = await fetch_oikotie(dry_run)
        results["oikotie_fetched"] = len(oikotie_records)
        if not oikotie_records:
            problems.append("Oikotie returned 0 listings (token or API failure?)")

    if dry_run:
        logger.info("Dry run — skipping DB writes")
        results["dry_run_complete"] = True
        return results

    # Write to DB
    engine = create_async_engine(DB_URL, echo=False)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with SessionFactory() as session:
            statfi_written = 0
            paavo_written = 0

            if statfi_records:
                try:
                    statfi_written = await write_statfi_to_db(session, statfi_records)
                except Exception as e:
                    problems.append(f"StatFi DB write failed: {e}")
                    logger.exception("StatFi DB write failed")
            if paavo_records:
                try:
                    paavo_written = await write_paavo_to_db(session, paavo_records)
                except Exception as e:
                    problems.append(f"Paavo DB write failed: {e}")
                    logger.exception("Paavo DB write failed")

            await session.commit()
            results["statfi_written"] = statfi_written
            results["paavo_written"] = paavo_written
            logger.info("DB writes: statfi=%d, paavo=%d", statfi_written, paavo_written)

        # Oikotie listings in separate session (change tracking)
        if oikotie_records:
            try:
                async with SessionFactory() as session:
                    oikotie_stats = await write_oikotie_to_db(session, oikotie_records, fetched_cities=oikotie_fetched_cities)
                    await session.commit()
                    results["oikotie_new"] = oikotie_stats.get("new", 0)
                    results["oikotie_updated"] = oikotie_stats.get("updated", 0)
                    results["oikotie_price_changes"] = oikotie_stats.get("price_changes", 0)
                    results["oikotie_removed"] = oikotie_stats.get("removed", 0)
                    logger.info(
                        "Oikotie DB: new=%d, updated=%d, price_changes=%d, removed=%d",
                        oikotie_stats.get("new", 0),
                        oikotie_stats.get("updated", 0),
                        oikotie_stats.get("price_changes", 0),
                        oikotie_stats.get("removed", 0),
                    )

                    # Snapshot current listing prices for history tracking.
                    # living_area_m2 lives on listing, not property_asset.
                    try:
                        snapshot_sql = text("""
                            INSERT INTO property.listing_price_snapshot (listing_id, asking_price, price_per_m2, snapshot_date, run_id)
                            SELECT l.listing_id, l.asking_price,
                                   CASE WHEN l.living_area_m2 > 0 THEN l.asking_price / l.living_area_m2 END,
                                   CURRENT_DATE, :run_id
                            FROM property.listing l
                            WHERE l.status = 'active' AND l.asking_price > 0
                            ON CONFLICT (listing_id, snapshot_date) DO UPDATE SET
                                asking_price = EXCLUDED.asking_price,
                                price_per_m2 = EXCLUDED.price_per_m2
                        """)
                        await session.execute(snapshot_sql, {"run_id": run_id})
                        await session.commit()
                    except Exception as e:
                        logger.warning("Listing price snapshot failed: %s", e)
            except Exception as e:
                problems.append(f"Oikotie DB write failed: {e}")
                logger.exception("Oikotie DB write failed")

        # Hintatiedot.fi transactions (KVKL realized prices)
        if "hintatiedot" in enabled_sources:
            try:
                ht_connector = HintatiedotConnector(HintatiedotConfig(delay_between_requests=0.5))
                from jarvis_property_intel.connectors.oikotie.connector import LOCATION_IDS
                ht_all: list[NormalizedRecord] = []
                # Deduplicate city names (LOCATION_IDS values have city name at index 2)
                city_names = sorted({loc[2] for loc in LOCATION_IDS.values()})
                for city_name in city_names:
                    try:
                        recs = await ht_connector.fetch_city(city_name)
                        ht_all.extend(recs)
                    except Exception as e:
                        logger.error("Hintatiedot fetch failed for %s: %s", city_name, e)
                await ht_connector.close()
                results["hintatiedot_fetched"] = len(ht_all)

                if ht_all:
                    async with SessionFactory() as session:
                        ht_stats = await write_hintatiedot_to_db(session, ht_all, run_id=run_id)
                        await session.commit()
                        results["hintatiedot_written"] = ht_stats["written"]
                        results["hintatiedot_new"] = ht_stats["new"]
                        results["hintatiedot_updated"] = ht_stats["updated"]
                        results["hintatiedot_changed"] = ht_stats["changed"]
                        logger.info(
                            "Hintatiedot DB: %d written (%d new, %d updated, %d fields changed)",
                            ht_stats["written"], ht_stats["new"], ht_stats["updated"], ht_stats["changed"],
                        )
            except Exception as e:
                problems.append(f"Hintatiedot failed: {e}")
                logger.exception("Hintatiedot pipeline failed")

        # MML kauppahintarekisteri transactions (real deed dates, precision=exact).
        if "mml" in enabled_sources and mml_records:
            try:
                async with SessionFactory() as session:
                    mml_stats = await write_mml_to_db(session, mml_records, run_id=run_id)
                    await session.commit()
                    results["mml_written"] = mml_stats["written"]
                    results["mml_new"] = mml_stats["new"]
                    results["mml_matched"] = mml_stats["matched"]
                    logger.info(
                        "MML DB: %d written (%d new, %d asset-matched)",
                        mml_stats["written"], mml_stats["new"], mml_stats["matched"],
                    )
            except Exception as e:
                problems.append(f"MML DB write failed: {e}")
                logger.exception("MML DB write failed")

        # Reverse-geocode any property_asset rows missing postal_code (lat/lon → postal_code).
        # Runs before view refresh so market_velocity_by_postal_code aggregations stay correct.
        try:
            async with SessionFactory() as session:
                filled = await fill_missing_postal_codes(session)
                await session.commit()
                results["postal_code_backfilled"] = filled
                if filled:
                    logger.info("Reverse-geocoded postal_code for %d asset(s)", filled)
        except Exception as e:
            problems.append(f"postal_code reverse-geocode failed: {e}")
            logger.exception("postal_code reverse-geocode failed")

        # Enrich active listings with /api/card/{id} detail data (fees, condition,
        # heating, energy, lift/sauna). Bounded budget per run to avoid blowing
        # up wall-clock time; one-shot backfill via scripts/backfill_listing_details.py.
        try:
            detail_budget = int(os.getenv("OIKOTIE_DETAIL_BUDGET_PER_RUN", "100"))
            async with SessionFactory() as session:
                detail_stats = await enrich_oikotie_details(session, max_records=detail_budget)
                await session.commit()
                results["oikotie_detail_fetched"] = detail_stats["fetched"]
                results["oikotie_detail_updated"] = detail_stats["updated"]
                results["oikotie_detail_failed"] = detail_stats["fetch_failed"]
                if detail_stats["updated"]:
                    logger.info("Detail-enriched %d listing(s)", detail_stats["updated"])
        except Exception as e:
            problems.append(f"oikotie detail-enrich failed: {e}")
            logger.exception("oikotie detail-enrich failed")

        # Refresh gold views
        await refresh_gold_views(engine)
        results["views_refreshed"] = True

        # ECB SDW: interest rates (Euribor + ECB MRO/DFR) + BoF housing-loan
        # market. Both are idempotent ON CONFLICT upserts — joka ajolla kaikki
        # Euriborit ja BoF-asuntolainamittarit re-fetchataan ja synkronoidaan.
        # Failures are warnings, not pipeline-blocking (ECB SDW can flap).
        if not dry_run:
            for label, script in (
                ("interest_rates", "fetch_interest_rates.py"),
                ("bof_loans", "fetch_bof_loans.py"),
            ):
                script_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), script
                )
                try:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, script_path,
                        env={**os.environ, "JARVIS_PROPERTY_INTEL_DATABASE_URL": DB_URL},
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
                    if proc.returncode == 0:
                        # Last line of these scripts is the summary
                        last = (stdout.decode(errors="replace").strip().splitlines() or [""])[-1]
                        logger.info("%s OK: %s", label, last[-200:])
                        results[f"{label}_status"] = "ok"
                    else:
                        problems.append(
                            f"{label} fetch exit={proc.returncode}: "
                            f"{stdout.decode(errors='replace')[-200:]}"
                        )
                        results[f"{label}_status"] = f"exit_{proc.returncode}"
                except asyncio.TimeoutError:
                    problems.append(f"{label} fetch timed out (>5 min)")
                    results[f"{label}_status"] = "timeout"
                except Exception as e:
                    problems.append(f"{label} fetch error: {e}")
                    results[f"{label}_status"] = "error"

        # Summary counts
        async with SessionFactory() as session:
            for tbl in ["property.area_snapshot", "property.listing",
                        "property.listing_event", "property.property_asset",
                        "property.latest_listing_state",
                        "property.market_velocity_by_postal_code",
                        "property.transaction"]:
                r = await session.execute(text(f"SELECT count(*) FROM {tbl}"))
                results[f"count_{tbl.split('.')[-1]}"] = r.scalar()

    except Exception as e:
        problems.append(f"DB connection failed: {e}")
        logger.exception("Database connection/session failed")

    finally:
        await engine.dispose()

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    results["elapsed_seconds"] = round(elapsed, 1)
    results["problems"] = problems
    logger.info("Pipeline complete in %.1fs: %s", elapsed, json.dumps(results, default=str))

    # Log pipeline completion
    try:
        elapsed_total = (datetime.now(timezone.utc) - t0).total_seconds()
        _track_engine = create_async_engine(DB_URL, echo=False)
        _track_sf = async_sessionmaker(_track_engine, class_=AsyncSession, expire_on_commit=False)
        async with _track_sf() as session:
            await session.execute(text("""
                UPDATE property.pipeline_run SET
                    completed_at = :completed_at,
                    status = :status,
                    records_fetched = :fetched,
                    records_written = :written,
                    records_changed = :changed,
                    problems_json = :problems,
                    elapsed_seconds = :elapsed,
                    results_json = :results_json
                WHERE run_id = :run_id
            """), {
                "run_id": run_id,
                "completed_at": datetime.now(timezone.utc),
                "status": "failed" if problems else "completed",
                "fetched": results.get("hintatiedot_fetched", 0) + results.get("oikotie_fetched", 0) + results.get("statfi_fetched", 0),
                "written": results.get("hintatiedot_written", 0) + results.get("oikotie_new", 0) + results.get("oikotie_updated", 0) + results.get("statfi_written", 0),
                "changed": results.get("hintatiedot_changed", 0) + results.get("oikotie_price_changes", 0),
                "problems": json.dumps(problems) if problems else None,
                "elapsed": elapsed_total,
                "results_json": json.dumps(results, default=str),
            })
            await session.commit()
        await _track_engine.dispose()
    except Exception as e:
        logger.debug("Pipeline run tracking failed: %s", e)

    # ── Telegram alerting ─────────────────────────────────────────────
    if problems:
        msg = (
            "*Property Intel Pipeline Alert*\n\n"
            f"*{len(problems)} problem(s) detected:*\n"
            + "\n".join(f"  - {p}" for p in problems)
            + f"\n\n_Elapsed: {results['elapsed_seconds']}s_"
        )
        send_telegram_alert(msg)

    return results


def main():
    parser = argparse.ArgumentParser(description="Property Intel hourly pipeline")
    parser.add_argument("--sources", type=str, default=None,
                        help="Comma-separated list of sources (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data but don't write to DB")
    parser.add_argument("--log-level", type=str, default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    sources = args.sources.split(",") if args.sources else None

    try:
        results = asyncio.run(run_pipeline(sources=sources, dry_run=args.dry_run))
    except Exception:
        tb = traceback.format_exc()
        logger.critical("Pipeline crashed:\n%s", tb)
        send_telegram_alert(
            "*Property Intel Pipeline CRASHED*\n\n"
            f"```\n{tb[-500:]}\n```"
        )
        return 1

    # Print summary
    print(json.dumps(results, indent=2, default=str))
    return 0 if results.get("elapsed_seconds") else 1


if __name__ == "__main__":
    sys.exit(main())
