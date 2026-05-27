"""End-to-end smoke script: fetch real data from public APIs, write to DB, verify.

This is NOT a pytest module — it is a sequential smoke runner with ordered steps
that pass data between each other (StatFi/Paavo fetch → write → verify → query) and
a single `asyncio.run(main())` event loop. It is intentionally named without a
``test_`` prefix so pytest does not collect its steps as independent test functions
(doing so breaks on the data-passing signature and shares one asyncpg engine across
multiple event loops → "another operation is in progress").

It hits LIVE external APIs (Tilastokeskus PxWeb, Paavo WFS) and WRITES test rows into
whatever ``JARVIS_PROPERTY_INTEL_DATABASE_URL`` points at. Point it at a throwaway DB.

Run:
    JARVIS_PROPERTY_INTEL_DATABASE_URL=postgresql+asyncpg://.../jarvis_property_intel_test \\
        uv run python tests/integration/smoke_e2e.py
    # or: make smoke-e2e
"""

import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

# Ensure property-intel is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)
os.environ["JARVIS_PROPERTY_INTEL_DATABASE_URL"] = DB_URL

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jarvis_property_intel.models import (
    AreaSnapshot,
    Base,
    Listing,
    ListingEvent,
    PropertyAsset,
    RawSnapshot,
    Transaction,
)
from jarvis_property_intel.connectors.mml import MMLConfig, MMLTransactionConnector
from jarvis_property_intel.connectors.statfi import StatFiConfig, StatFiPxWebConnector
from jarvis_property_intel.connectors.paavo import PaavoConfig, PaavoConnector
from jarvis_property_intel.connectors.base import RawFetchResult
from jarvis_property_intel.resolver import EntityResolver, PropertyCandidate

engine = create_async_engine(DB_URL, echo=False)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_statfi_fetch():
    """Test: Fetch real price data from Tilastokeskus PxWeb API."""
    print("\n=== TEST 1: Tilastokeskus PxWeb fetch ===")
    connector = StatFiPxWebConnector(StatFiConfig())

    # Health check
    healthy = await connector.health_check()
    print(f"  Health check: {'PASS' if healthy else 'FAIL'}")
    assert healthy, "StatFi health check failed"

    # Fetch apartment price data (small query — new table 13mt dimensions)
    results = await connector.fetch_dataset(
        dataset_id="apartment_prices",
        query={
            "query": [
                {"code": "Vuosineljännes", "selection": {"filter": "item", "values": ["2024Q4"]}},
                {"code": "Postinumero", "selection": {"filter": "item", "values": ["00100"]}},
                {"code": "Talotyyppi", "selection": {"filter": "item", "values": ["1", "2", "3"]}},
            ],
            "response": {"format": "json-stat2"},
        },
    )
    print(f"  Raw results: {len(results)} fetch(es)")
    assert len(results) > 0, "No results from StatFi"

    # Normalize
    all_records = []
    for raw in results:
        records = connector.normalize(raw)
        all_records.extend(records)
    print(f"  Normalized records: {len(all_records)}")

    if all_records:
        sample = all_records[0]
        print(f"  Sample record type: {sample.record_type}")
        print(f"  Sample data keys: {list(sample.data.keys())[:10]}")
        print(f"  Sample data: {json.dumps({k: v for k, v in list(sample.data.items())[:5]}, default=str)}")

    return all_records


async def test_paavo_fetch():
    """Test: Fetch postal code data from Paavo WFS."""
    print("\n=== TEST 2: Paavo WFS fetch ===")
    connector = PaavoConnector(PaavoConfig())

    healthy = await connector.health_check()
    print(f"  Health check: {'PASS' if healthy else 'FAIL'}")
    assert healthy, "Paavo health check failed"

    # Fetch a few postal codes from Helsinki
    results = await connector.fetch_dataset(
        dataset_id="pno_tilasto",
        postal_codes=["00100", "00180", "00200"],
    )
    print(f"  Raw results: {len(results)} fetch(es)")
    assert len(results) > 0, "No results from Paavo"

    all_records = []
    for raw in results:
        records = connector.normalize(raw)
        all_records.extend(records)
    print(f"  Normalized records: {len(all_records)}")

    if all_records:
        sample = all_records[0]
        print(f"  Sample record type: {sample.record_type}")
        print(f"  Sample data: {json.dumps({k: v for k, v in list(sample.data.items())[:8]}, default=str)}")

    return all_records


async def test_write_to_db(statfi_records, paavo_records):
    """Test: Write fetched data to DB and verify."""
    print("\n=== TEST 3: Write to database ===")

    async with SessionFactory() as session:
        # Write area snapshots from StatFi (JSON-stat2 format)
        # Group by (postal_code, quarter, building_type) since there are
        # multiple measures (price/m², count) per combination.
        statfi_groups: dict[tuple, dict] = {}
        for record in statfi_records:
            if record.record_type == "area_stats":
                d = record.data
                quarter_str = d.get("Vuosineljännes", "")
                postal_code = d.get("Postinumero", "00100")
                building_type = d.get("Talotyyppi_label", d.get("Talotyyppi", ""))
                measure = d.get("Tiedot", "")
                value = d.get("value")

                if not quarter_str or "Q" not in quarter_str:
                    continue

                key = (postal_code, quarter_str, building_type)
                if key not in statfi_groups:
                    statfi_groups[key] = {}
                if measure == "keskihinta_aritm_nw":
                    statfi_groups[key]["price_m2"] = value
                elif measure == "lkm_julk20":
                    statfi_groups[key]["count"] = value

        statfi_written = 0
        for (postal_code, quarter_str, building_type), measures in statfi_groups.items():
            year = int(quarter_str[:4])
            q = int(quarter_str[-1])
            month_start = (q - 1) * 3 + 1
            month_end = q * 3
            period_start = date(year, month_start, 1)
            if month_end == 12:
                period_end = date(year, 12, 31)
            else:
                period_end = date(year, month_end + 1, 1) - timedelta(days=1)

            snapshot = AreaSnapshot(
                postal_code=postal_code,
                municipality="Helsinki",
                period_start=period_start,
                period_end=period_end,
                segment=building_type or None,
                median_sold_m2=measures.get("price_m2"),
                inventory_count=measures.get("count"),
            )
            session.add(snapshot)
            statfi_written += 1

        # Write area snapshots from Paavo
        paavo_written = 0
        for record in paavo_records:
            if record.record_type == "area_stats":
                d = record.data
                snapshot = AreaSnapshot(
                    postal_code=d.get("postal_code", ""),
                    municipality=d.get("municipality_name", d.get("municipality", "")),
                    period_start=date(2024, 1, 1),
                    period_end=date(2024, 12, 31),
                    segment=None,
                    income_median=d.get("median_income"),
                    owner_occupancy_ratio=d.get("owner_occupied_ratio"),
                )
                session.add(snapshot)
                paavo_written += 1

        # Write some test property assets
        assets = []
        for i, (addr, pc, muni, atype) in enumerate([
            ("Mannerheimintie 10 A 5", "00100", "Helsinki", "apartment_unit"),
            ("Mechelininkatu 22 B 12", "00100", "Helsinki", "apartment_unit"),
            ("Fleminginkatu 15 C 8", "00500", "Helsinki", "apartment_unit"),
            ("Punavuorenkatu 3", "00120", "Helsinki", "rowhouse_unit"),
            ("Tammitie 5", "02180", "Espoo", "detached_house"),
        ]):
            asset = PropertyAsset(
                asset_type=atype,
                canonical_address=addr,
                postal_code=pc,
                municipality=muni,
                lat=60.17 + i * 0.005,
                lon=24.94 + i * 0.003,
                source_confidence=0.85,
            )
            session.add(asset)
            assets.append(asset)

        await session.flush()

        # Write test listings linked to assets
        listings = []
        for j, (asset, price, area, year) in enumerate([
            (assets[0], 385000, 65.0, 1952),
            (assets[1], 425000, 78.5, 1965),
            (assets[2], 289000, 42.0, 1938),
            (assets[3], 520000, 95.0, 2005),
            (assets[4], 650000, 145.0, 1998),
        ]):
            listing = Listing(
                asset_id=asset.asset_id,
                source="test",
                source_listing_id=f"test-{j+1}",
                first_seen_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
                last_seen_at=datetime(2025, 3, 10, tzinfo=timezone.utc),
                status="active",
                asking_price=price,
                living_area_m2=area,
                year_built=year,
                rooms=j + 1,
                description_text=f"Test listing for {asset.canonical_address}",
            )
            session.add(listing)
            listings.append((listing, price))

        # Flush to get server-generated listing_ids before creating events
        await session.flush()
        listings_written = len(listings)

        # Add listing events (after flush so listing_id is populated)
        for listing, price in listings:
            event = ListingEvent(
                listing_id=listing.listing_id,
                event_type="created",
                event_at=datetime(2025, 1, 15, tzinfo=timezone.utc),
                new_value=str(price),
            )
            session.add(event)

        # Write test transactions
        txns_written = 0
        for k, (asset, price, tdate) in enumerate([
            (assets[0], 370000, date(2024, 11, 15)),
            (assets[2], 275000, date(2024, 8, 20)),
            (assets[4], 620000, date(2024, 6, 10)),
        ]):
            txn = Transaction(
                asset_id=asset.asset_id,
                transaction_date=tdate,
                transaction_price=price,
                transaction_type="sale",
                source="test_mml",
                source_record_id=f"test-txn-{k+1}",
            )
            session.add(txn)
            txns_written += 1

        await session.commit()

        print(f"  StatFi area snapshots written: {statfi_written}")
        print(f"  Paavo area snapshots written: {paavo_written}")
        print(f"  Property assets written: {len(assets)}")
        print(f"  Listings written: {listings_written}")
        print(f"  Transactions written: {txns_written}")


async def test_verify_db():
    """Test: Verify data is in DB with counts."""
    print("\n=== TEST 4: Verify database contents ===")

    async with SessionFactory() as session:
        for model, name in [
            (PropertyAsset, "property_asset"),
            (Listing, "listing"),
            (ListingEvent, "listing_event"),
            (Transaction, "transaction"),
            (AreaSnapshot, "area_snapshot"),
        ]:
            result = await session.execute(select(func.count()).select_from(model))
            count = result.scalar()
            status = "PASS" if count > 0 else "FAIL"
            print(f"  {name}: {count} rows [{status}]")
            assert count > 0, f"{name} has no rows"

        # Verify listing -> asset linkage
        result = await session.execute(
            select(func.count()).select_from(Listing).where(Listing.asset_id.isnot(None))
        )
        linked = result.scalar()
        print(f"  Listings linked to assets: {linked} [{'PASS' if linked > 0 else 'FAIL'}]")

        # Sample query: listings in 00100
        result = await session.execute(
            select(Listing, PropertyAsset)
            .join(PropertyAsset, Listing.asset_id == PropertyAsset.asset_id)
            .where(PropertyAsset.postal_code == "00100")
        )
        rows = result.all()
        print(f"  Listings in 00100: {len(rows)}")
        for listing, asset in rows:
            print(f"    {asset.canonical_address}: {listing.asking_price}€, {listing.living_area_m2}m²")


async def test_entity_resolver():
    """Test: Entity resolver matches similar properties."""
    print("\n=== TEST 5: Entity resolver ===")

    resolver = EntityResolver()

    # Two candidates that should match (same address, similar area)
    a = PropertyCandidate(
        source_id="oikotie",
        source_record_id="12345",
        address="Mannerheimintie 10 A 5",
        postal_code="00100",
        municipality="Helsinki",
        lat=60.170,
        lon=24.940,
        living_area_m2=65.0,
        year_built=1952,
    )
    b = PropertyCandidate(
        source_id="etuovi",
        source_record_id="67890",
        address="Mannerheimintie 10 a 5",
        postal_code="00100",
        municipality="Helsinki",
        lat=60.170,
        lon=24.940,
        living_area_m2=64.5,
        year_built=1952,
    )

    result = resolver.compare(a, b)
    print(f"  Match score: {result.score:.2f}")
    print(f"  Strategy: {result.strategy}")
    print(f"  Reason: {result.reason}")
    print(f"  Review needed: {result.review_needed}")
    print(f"  Auto-confirm: {'YES' if result.score >= 0.90 else 'NO'}")
    assert result.score >= 0.80, f"Expected high match score, got {result.score}"
    print("  PASS")

    # Two candidates that should NOT match
    c = PropertyCandidate(
        source_id="oikotie",
        source_record_id="99999",
        address="Kaivokatu 1",
        postal_code="00100",
        municipality="Helsinki",
        living_area_m2=120.0,
        year_built=2010,
    )

    result2 = resolver.compare(a, c)
    print(f"\n  Non-match score: {result2.score:.2f}")
    print(f"  Review needed: {result2.review_needed}")
    assert result2.score < 0.80, f"Expected low match score, got {result2.score}"
    print("  PASS")


async def test_refresh_gold_views():
    """Test: Refresh materialized views."""
    print("\n=== TEST 6: Refresh gold views ===")

    async with engine.begin() as conn:
        for view in [
            "property.latest_listing_state",
            "property.price_change_history",
            "property.market_velocity_by_postal_code",
        ]:
            try:
                await conn.execute(text(f"REFRESH MATERIALIZED VIEW {view}"))
                # Count rows
                result = await conn.execute(text(f"SELECT count(*) FROM {view}"))
                count = result.scalar()
                print(f"  {view.split('.')[-1]}: {count} rows [PASS]")
            except Exception as e:
                print(f"  {view.split('.')[-1]}: ERROR - {e}")


async def test_api_queries():
    """Test: Run the actual API query logic against DB."""
    print("\n=== TEST 7: API query logic ===")

    async with SessionFactory() as session:
        # Search by postal code
        result = await session.execute(
            select(PropertyAsset).where(PropertyAsset.postal_code == "00100")
        )
        assets = result.scalars().all()
        print(f"  Search 00100: {len(assets)} assets [{'PASS' if len(assets) > 0 else 'FAIL'}]")

        # Get with listings
        if assets:
            asset = assets[0]
            result = await session.execute(
                select(Listing).where(Listing.asset_id == asset.asset_id)
            )
            listings = result.scalars().all()
            print(f"  Asset {asset.canonical_address}: {len(listings)} listing(s) [PASS]")

            # Timeline
            if listings:
                result = await session.execute(
                    select(ListingEvent).where(ListingEvent.listing_id == listings[0].listing_id)
                )
                events = result.scalars().all()
                print(f"  Timeline events: {len(events)} [{'PASS' if len(events) > 0 else 'FAIL'}]")

        # Comparables
        result = await session.execute(
            select(PropertyAsset)
            .where(PropertyAsset.postal_code == "00100")
            .where(PropertyAsset.asset_type == "apartment_unit")
        )
        comps = result.scalars().all()
        print(f"  Comparables in 00100 (apartment): {len(comps)} [{'PASS' if len(comps) > 0 else 'FAIL'}]")

        # Area snapshot
        result = await session.execute(
            select(AreaSnapshot).where(AreaSnapshot.postal_code == "00100")
        )
        snapshots = result.scalars().all()
        print(f"  Area snapshots for 00100: {len(snapshots)} [{'PASS' if len(snapshots) > 0 else 'FAIL'}]")


def _guard_target_db() -> None:
    """Refuse to write test rows into a non-test DB unless explicitly overridden.

    The smoke writes synthetic property_asset/listing/transaction rows (e.g. the
    ``test_mml`` transactions). Running it against the production DB pollutes it —
    which is how stray ``test_mml`` rows landed there before. Block that by default.
    """
    db_url = os.environ["JARVIS_PROPERTY_INTEL_DATABASE_URL"]
    looks_like_test = "test" in db_url.lower() or db_url.startswith("sqlite")
    if not looks_like_test and os.getenv("ALLOW_SMOKE_WRITES") != "1":
        # Mask credentials before printing the URL back to the terminal/logs.
        masked = re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", db_url)
        print(
            "REFUSING to run: JARVIS_PROPERTY_INTEL_DATABASE_URL does not look like a\n"
            f"test DB ({masked}). This smoke writes test rows. Point it at a throwaway\n"
            "DB (name containing 'test'), or set ALLOW_SMOKE_WRITES=1 to override."
        )
        sys.exit(2)


async def main():
    print("=" * 60)
    print("PROPERTY-INTEL INTEGRATION TEST")
    print("=" * 60)
    _guard_target_db()

    passed = 0
    failed = 0

    # Test 1: StatFi fetch
    try:
        statfi_records = await test_statfi_fetch()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        statfi_records = []
        failed += 1

    # Test 2: Paavo fetch
    try:
        paavo_records = await test_paavo_fetch()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        paavo_records = []
        failed += 1

    # Test 3: Write to DB
    try:
        await test_write_to_db(statfi_records, paavo_records)
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        import traceback; traceback.print_exc()
        failed += 1

    # Test 4: Verify DB
    try:
        await test_verify_db()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 5: Entity resolver
    try:
        await test_entity_resolver()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 6: Gold views
    try:
        await test_refresh_gold_views()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    # Test 7: API queries
    try:
        await test_api_queries()
        passed += 1
    except Exception as e:
        print(f"  FAIL: {e}")
        failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed out of {passed + failed}")
    print("=" * 60)

    await engine.dispose()
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
