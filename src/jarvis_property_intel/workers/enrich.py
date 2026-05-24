"""Property Intel — Enrichment Worker.

Processes raw bronze-layer data into normalized silver-layer entities,
runs entity resolution, and refreshes gold-layer materialized views.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.models import (
    AreaSnapshot,
    EntityMatch,
    Listing,
    ListingEvent,
    PropertyAsset,
    RawSnapshot,
    Transaction,
)
from jarvis_property_intel.db import get_engine, get_session
from jarvis_property_intel.connectors import ConnectorRegistry, NormalizedRecord, RawFetchResult
from jarvis_property_intel.resolver import EntityResolver, PropertyCandidate
from jarvis_property_intel.resolver.strategies import normalize_finnish_address

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level registry — populated by the caller or via _get_registry()
# ---------------------------------------------------------------------------

_registry: ConnectorRegistry | None = None

BATCH_SIZE = int(os.getenv("ENRICH_BATCH_SIZE", "100"))
NOMINATIM_USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT", "property-intel/0.1.0 (JARVIS enrichment worker)"
)
NOMINATIM_RATE_LIMIT_S = 1.0  # Nominatim policy: max 1 request/second


def set_registry(registry: ConnectorRegistry) -> None:
    """Allow callers to inject a pre-configured ConnectorRegistry."""
    global _registry
    _registry = registry


def _get_registry() -> ConnectorRegistry:
    """Return the module-level registry, creating an empty one if needed."""
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    return _registry


# ===================================================================
# 1. normalize_raw_snapshots
# ===================================================================


async def normalize_raw_snapshots() -> int:
    """Parse bronze snapshots into silver entities.

    Reads unprocessed raw_snapshot records (those without corresponding
    entries in the silver-layer tables), loads raw content from the
    filesystem, runs the appropriate connector's normalize() method,
    and upserts into listing / transaction / area_snapshot tables.

    Returns count of records processed.
    """
    registry = _get_registry()
    total_processed = 0

    async with get_session() as session:
        # Find raw_snapshots that haven't been normalized yet.
        # We use a LEFT JOIN approach: snapshots with no matching listing
        # AND no matching transaction are considered unprocessed.
        # For simplicity we use a subquery exclusion approach.
        processed_listing_ids = (
            select(Listing.source, Listing.source_listing_id)
        ).subquery()
        processed_txn_ids = (
            select(Transaction.source, Transaction.source_record_id)
        ).subquery()

        # Query raw snapshots in batches
        offset = 0
        while True:
            stmt = (
                select(RawSnapshot)
                .order_by(RawSnapshot.fetched_at.asc())
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            result = await session.execute(stmt)
            snapshots = list(result.scalars().all())

            if not snapshots:
                break

            for snapshot in snapshots:
                try:
                    processed = await _process_single_snapshot(
                        session, registry, snapshot
                    )
                    total_processed += processed
                except Exception:
                    logger.exception(
                        "Failed to process snapshot %s from source '%s'",
                        snapshot.snapshot_id,
                        snapshot.source,
                    )
                    # Continue with next snapshot — one failure shouldn't
                    # stop the pipeline.
                    continue

            # Flush after each batch to avoid holding too many objects
            await session.flush()
            offset += BATCH_SIZE

    logger.info("normalize_raw_snapshots: processed %d records", total_processed)
    return total_processed


async def _process_single_snapshot(
    session: AsyncSession,
    registry: ConnectorRegistry,
    snapshot: RawSnapshot,
) -> int:
    """Process one raw_snapshot row and return count of records created."""
    connector = registry.get(snapshot.source)
    if connector is None:
        logger.warning(
            "No connector registered for source '%s', skipping snapshot %s",
            snapshot.source,
            snapshot.snapshot_id,
        )
        return 0

    if not registry.is_enabled(snapshot.source):
        logger.debug(
            "Connector '%s' is disabled, skipping snapshot %s",
            snapshot.source,
            snapshot.snapshot_id,
        )
        return 0

    # Read and decompress the raw content from the filesystem
    storage_path = Path(snapshot.storage_path)
    if not storage_path.exists():
        logger.warning(
            "Storage path does not exist: %s (snapshot %s)",
            storage_path,
            snapshot.snapshot_id,
        )
        return 0

    raw_bytes = storage_path.read_bytes()
    try:
        decompressed = zlib.decompress(raw_bytes)
    except zlib.error:
        # Content might not be compressed — use as-is
        logger.debug(
            "Content not zlib-compressed for snapshot %s, using raw bytes",
            snapshot.snapshot_id,
        )
        decompressed = raw_bytes

    # Build a RawFetchResult for the connector
    raw_result = RawFetchResult(
        source_id=snapshot.source,
        fetched_at=snapshot.fetched_at,
        raw_content=decompressed,
        content_type="application/octet-stream",
        parse_version=snapshot.parse_version,
        url=snapshot.url,
        source_record_id=snapshot.source_record_id,
    )

    # Normalize via the connector
    try:
        normalized_records: list[NormalizedRecord] = connector.normalize(raw_result)
    except Exception:
        logger.exception(
            "Connector '%s' normalize() failed for snapshot %s",
            snapshot.source,
            snapshot.snapshot_id,
        )
        return 0

    count = 0
    for record in normalized_records:
        try:
            await _upsert_normalized_record(session, record)
            count += 1
        except Exception:
            logger.exception(
                "Failed to upsert normalized record %s/%s",
                record.source_id,
                record.source_record_id,
            )

    return count


async def _upsert_normalized_record(
    session: AsyncSession, record: NormalizedRecord
) -> None:
    """Map a NormalizedRecord to the appropriate ORM model and upsert."""
    data = record.data

    if record.record_type == "listing":
        await _upsert_listing(session, record)
    elif record.record_type == "transaction":
        await _upsert_transaction(session, record)
    elif record.record_type == "area_stats":
        await _upsert_area_snapshot(session, record)
    else:
        logger.warning(
            "Unknown record_type '%s' for %s/%s",
            record.record_type,
            record.source_id,
            record.source_record_id,
        )


async def _upsert_listing(session: AsyncSession, record: NormalizedRecord) -> None:
    """Upsert a listing record with ON CONFLICT for idempotency."""
    d = record.data
    now = datetime.now(timezone.utc)

    values = {
        "source": record.source_id,
        "source_listing_id": record.source_record_id,
        "first_seen_at": d.get("first_seen_at", record.fetched_at),
        "last_seen_at": d.get("last_seen_at", record.fetched_at),
        "status": d.get("status", "active"),
        "asking_price": d.get("asking_price"),
        "living_area_m2": d.get("living_area_m2"),
        "year_built": d.get("year_built"),
        "rooms": d.get("rooms"),
        "lot_area_m2": d.get("lot_area_m2"),
        "description_text": d.get("description_text"),
        "energy_class": d.get("energy_class"),
        "json_blob": d.get("json_blob"),
    }

    stmt = pg_insert(Listing).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_listing_source",
        set_={
            "last_seen_at": values["last_seen_at"],
            "status": values["status"],
            "asking_price": values["asking_price"],
            "living_area_m2": values["living_area_m2"],
            "year_built": values["year_built"],
            "rooms": values["rooms"],
            "lot_area_m2": values["lot_area_m2"],
            "description_text": values["description_text"],
            "energy_class": values["energy_class"],
            "json_blob": values["json_blob"],
        },
    )

    result = await session.execute(stmt)

    # Create a "created" ListingEvent for new listings.
    # Check if the listing was inserted (not updated) by querying it.
    listing_stmt = select(Listing).where(
        Listing.source == record.source_id,
        Listing.source_listing_id == record.source_record_id,
    )
    listing_result = await session.execute(listing_stmt)
    listing = listing_result.scalar_one_or_none()

    if listing is not None:
        # Check if a "created" event already exists
        event_check = select(ListingEvent).where(
            ListingEvent.listing_id == listing.listing_id,
            ListingEvent.event_type == "created",
        )
        event_result = await session.execute(event_check)
        if event_result.scalar_one_or_none() is None:
            event = ListingEvent(
                listing_id=listing.listing_id,
                event_type="created",
                event_at=listing.first_seen_at,
                new_value=str(listing.asking_price) if listing.asking_price else None,
            )
            session.add(event)


async def _upsert_transaction(
    session: AsyncSession, record: NormalizedRecord
) -> None:
    """Upsert a transaction record with ON CONFLICT for idempotency."""
    d = record.data

    txn_date = d.get("transaction_date")
    if isinstance(txn_date, str):
        txn_date = date.fromisoformat(txn_date)

    values = {
        "source": record.source_id,
        "source_record_id": record.source_record_id,
        "parcel_id": d.get("parcel_id"),
        "transaction_date": txn_date,
        "transaction_price": d.get("transaction_price", 0.0),
        "transaction_type": d.get("transaction_type", "unknown"),
    }

    stmt = pg_insert(Transaction).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_transaction_source",
        set_={
            "parcel_id": values["parcel_id"],
            "transaction_date": values["transaction_date"],
            "transaction_price": values["transaction_price"],
            "transaction_type": values["transaction_type"],
        },
    )
    await session.execute(stmt)


async def _upsert_area_snapshot(
    session: AsyncSession, record: NormalizedRecord
) -> None:
    """Upsert an area_snapshot record with ON CONFLICT for idempotency."""
    d = record.data

    period_start = d.get("period_start")
    period_end = d.get("period_end")
    if isinstance(period_start, str):
        period_start = date.fromisoformat(period_start)
    if isinstance(period_end, str):
        period_end = date.fromisoformat(period_end)

    values = {
        "postal_code": d.get("postal_code", ""),
        "municipality": d.get("municipality", ""),
        "period_start": period_start,
        "period_end": period_end,
        "segment": d.get("segment"),
        "median_ask_m2": d.get("median_ask_m2"),
        "median_sold_m2": d.get("median_sold_m2"),
        "dom_median": d.get("dom_median"),
        "inventory_count": d.get("inventory_count"),
        "price_cut_ratio": d.get("price_cut_ratio"),
        "income_median": d.get("income_median"),
        "owner_occupancy_ratio": d.get("owner_occupancy_ratio"),
    }

    stmt = pg_insert(AreaSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_area_snapshot_period",
        set_={
            "median_ask_m2": values["median_ask_m2"],
            "median_sold_m2": values["median_sold_m2"],
            "dom_median": values["dom_median"],
            "inventory_count": values["inventory_count"],
            "price_cut_ratio": values["price_cut_ratio"],
            "income_median": values["income_median"],
            "owner_occupancy_ratio": values["owner_occupancy_ratio"],
        },
    )
    await session.execute(stmt)


# ===================================================================
# 2. canonicalize_addresses
# ===================================================================


async def canonicalize_addresses() -> int:
    """Normalize addresses on property_asset records and geocode via Nominatim.

    - Applies Finnish address normalization rules.
    - Geocodes via OpenStreetMap Nominatim (max 1 req/s per their policy).

    Returns count of records updated.
    """
    updated = 0

    async with get_session() as session:
        # Query PropertyAssets that need geocoding (lat or lon is NULL)
        offset = 0
        while True:
            stmt = (
                select(PropertyAsset)
                .where(
                    (PropertyAsset.lat.is_(None)) | (PropertyAsset.lon.is_(None))
                )
                .order_by(PropertyAsset.created_at.asc())
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            result = await session.execute(stmt)
            assets = list(result.scalars().all())

            if not assets:
                break

            for asset in assets:
                try:
                    did_update = await _canonicalize_single_asset(session, asset)
                    if did_update:
                        updated += 1
                except Exception:
                    logger.exception(
                        "Failed to canonicalize/geocode asset %s (%s)",
                        asset.asset_id,
                        asset.canonical_address,
                    )
                    continue

            await session.flush()
            offset += BATCH_SIZE

    logger.info("canonicalize_addresses: updated %d records", updated)
    return updated


async def _canonicalize_single_asset(
    session: AsyncSession, asset: PropertyAsset
) -> bool:
    """Normalize address and geocode a single PropertyAsset. Returns True if updated."""
    # Step 1: Normalize the canonical address using Finnish rules
    original_address = asset.canonical_address
    normalized = normalize_finnish_address(original_address)

    if normalized != original_address:
        asset.canonical_address = normalized
        logger.debug(
            "Normalized address for %s: '%s' -> '%s'",
            asset.asset_id,
            original_address,
            normalized,
        )

    # Step 2: Geocode via Nominatim
    lat, lon = await _geocode_nominatim(normalized, asset.postal_code)

    if lat is not None and lon is not None:
        asset.lat = lat
        asset.lon = lon
        logger.debug(
            "Geocoded asset %s: (%f, %f)",
            asset.asset_id,
            lat,
            lon,
        )
        return True

    # Even if geocoding failed, we updated the canonical address
    if normalized != original_address:
        return True

    return False


async def _geocode_nominatim(
    address: str, postal_code: str | None
) -> tuple[float | None, float | None]:
    """Geocode an address using the Nominatim API.

    Returns (lat, lon) or (None, None) if geocoding fails.
    Respects Nominatim's rate limit of 1 request/second.
    """
    query_parts = [address]
    if postal_code:
        query_parts.append(postal_code)
    query_parts.append("Finland")
    query = ", ".join(query_parts)

    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
    }
    headers = {
        "User-Agent": NOMINATIM_USER_AGENT,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()

            results = response.json()
            if results and len(results) > 0:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                return lat, lon

            logger.debug("Nominatim returned no results for query: %s", query)
            return None, None

    except httpx.HTTPError as exc:
        logger.warning("Nominatim request failed for '%s': %s", query, exc)
        return None, None
    except (KeyError, ValueError, IndexError) as exc:
        logger.warning("Failed to parse Nominatim response for '%s': %s", query, exc)
        return None, None
    finally:
        # Rate-limit: wait to respect Nominatim's 1 req/s policy
        await asyncio.sleep(NOMINATIM_RATE_LIMIT_S)


# ===================================================================
# 3. run_entity_resolution
# ===================================================================


async def run_entity_resolution() -> int:
    """Run entity resolution on unlinked listings.

    - Queries listings where asset_id IS NULL.
    - Builds PropertyCandidate from each listing and each existing asset.
    - Runs EntityResolver.resolve_batch().
    - Auto-confirms high-confidence matches (score >= 0.90).
    - Inserts uncertain matches into entity_match table.
    - Creates new PropertyAsset for listings with no match.

    Returns count of matches/assignments made.
    """
    resolver = EntityResolver()
    match_count = 0

    async with get_session() as session:
        # Process unlinked listings in batches
        offset = 0
        while True:
            # Query unlinked listings
            listing_stmt = (
                select(Listing)
                .where(Listing.asset_id.is_(None))
                .order_by(Listing.first_seen_at.asc())
                .offset(offset)
                .limit(BATCH_SIZE)
            )
            listing_result = await session.execute(listing_stmt)
            unlinked_listings = list(listing_result.scalars().all())

            if not unlinked_listings:
                break

            # Gather distinct postal codes from the listing data
            # (postal codes live in the json_blob or need to be extracted)
            postal_codes: set[str] = set()
            for listing in unlinked_listings:
                pc = _extract_postal_code(listing)
                if pc:
                    postal_codes.add(pc)

            # Query existing assets in those postal codes
            existing_assets: list[PropertyAsset] = []
            if postal_codes:
                asset_stmt = select(PropertyAsset).where(
                    PropertyAsset.postal_code.in_(postal_codes)
                )
                asset_result = await session.execute(asset_stmt)
                existing_assets = list(asset_result.scalars().all())

            # Build PropertyCandidate lists
            listing_candidates: list[tuple[Listing, PropertyCandidate]] = []
            for listing in unlinked_listings:
                candidate = _listing_to_candidate(listing)
                listing_candidates.append((listing, candidate))

            asset_candidates: list[tuple[PropertyAsset, PropertyCandidate]] = []
            for asset in existing_assets:
                candidate = _asset_to_candidate(asset)
                asset_candidates.append((asset, candidate))

            # Run entity resolution
            new_candidates = [c for _, c in listing_candidates]
            existing_cands = [c for _, c in asset_candidates]

            if existing_cands:
                results = resolver.resolve_batch(new_candidates, existing_cands)
            else:
                results = []

            # Build lookup maps for linking back
            listing_by_key: dict[str, Listing] = {
                f"{l.source}:{l.source_listing_id}": l for l in unlinked_listings
            }
            asset_by_key: dict[str, PropertyAsset] = {
                f"asset:{a.asset_id}": a for a in existing_assets
            }

            # Track which listings got matched
            matched_listings: set[str] = set()

            for match_result in results:
                candidate_a = match_result.candidate_a
                candidate_b = match_result.candidate_b
                listing_key = (
                    f"{candidate_a.source_id}:{candidate_a.source_record_id}"
                )
                asset_key = (
                    f"{candidate_b.source_id}:{candidate_b.source_record_id}"
                )

                listing = listing_by_key.get(listing_key)
                asset = asset_by_key.get(asset_key)

                if listing is None or asset is None:
                    continue

                if match_result.score >= 0.90 and not match_result.review_needed:
                    # Auto-confirm: link the listing to the asset
                    listing.asset_id = asset.asset_id
                    matched_listings.add(listing_key)
                    match_count += 1
                    logger.debug(
                        "Auto-confirmed match: listing %s -> asset %s "
                        "(score=%.2f, strategy=%s)",
                        listing.listing_id,
                        asset.asset_id,
                        match_result.score,
                        match_result.strategy,
                    )
                elif match_result.review_needed:
                    # Uncertain match: insert into entity_match for review
                    # We need two assets to store in entity_match; create a
                    # temporary asset for the listing if needed, or record
                    # the match referencing the existing asset.
                    # Since entity_match requires two asset IDs, we create
                    # the new asset first, then record the pending match.
                    new_asset = await _create_asset_from_listing(session, listing)
                    listing.asset_id = new_asset.asset_id
                    matched_listings.add(listing_key)

                    entity_match = EntityMatch(
                        asset_id_a=new_asset.asset_id,
                        asset_id_b=asset.asset_id,
                        match_score=match_result.score,
                        match_reason=match_result.reason,
                        match_status="pending",
                    )
                    session.add(entity_match)
                    match_count += 1
                    logger.debug(
                        "Pending match: listing %s (new asset %s) <-> asset %s "
                        "(score=%.2f)",
                        listing.listing_id,
                        new_asset.asset_id,
                        asset.asset_id,
                        match_result.score,
                    )

            # Create new PropertyAssets for unmatched listings
            for listing in unlinked_listings:
                listing_key = f"{listing.source}:{listing.source_listing_id}"
                if listing_key not in matched_listings:
                    try:
                        new_asset = await _create_asset_from_listing(
                            session, listing
                        )
                        listing.asset_id = new_asset.asset_id
                        match_count += 1
                        logger.debug(
                            "Created new asset %s for unmatched listing %s",
                            new_asset.asset_id,
                            listing.listing_id,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to create asset for listing %s",
                            listing.listing_id,
                        )

            await session.flush()
            offset += BATCH_SIZE

    logger.info("run_entity_resolution: %d matches/assignments", match_count)
    return match_count


def _extract_postal_code(listing: Listing) -> str | None:
    """Extract postal code from listing's json_blob or other fields."""
    if listing.json_blob and isinstance(listing.json_blob, dict):
        pc = listing.json_blob.get("postal_code")
        if pc:
            return str(pc)
        # Try nested address data
        addr = listing.json_blob.get("address", {})
        if isinstance(addr, dict):
            pc = addr.get("postal_code")
            if pc:
                return str(pc)
    return None


def _listing_to_candidate(listing: Listing) -> PropertyCandidate:
    """Build a PropertyCandidate from a Listing ORM model."""
    blob = listing.json_blob or {}

    address = blob.get("address", blob.get("street_address"))
    if isinstance(address, dict):
        address = address.get("street", address.get("full"))

    return PropertyCandidate(
        source_id=listing.source,
        source_record_id=listing.source_listing_id,
        address=address if isinstance(address, str) else None,
        postal_code=_extract_postal_code(listing),
        municipality=blob.get("municipality"),
        lat=blob.get("lat"),
        lon=blob.get("lon"),
        living_area_m2=listing.living_area_m2,
        year_built=listing.year_built,
        rooms=listing.rooms,
        lot_area_m2=listing.lot_area_m2,
        parcel_id=blob.get("parcel_id"),
        building_id=blob.get("building_id"),
        housing_company_name=blob.get("housing_company_name"),
        apartment_number=blob.get("apartment_number"),
    )


def _asset_to_candidate(asset: PropertyAsset) -> PropertyCandidate:
    """Build a PropertyCandidate from a PropertyAsset ORM model."""
    return PropertyCandidate(
        source_id="asset",
        source_record_id=str(asset.asset_id),
        address=asset.canonical_address,
        postal_code=asset.postal_code,
        municipality=asset.municipality,
        lat=asset.lat,
        lon=asset.lon,
        parcel_id=asset.parcel_id,
        building_id=asset.building_id,
        housing_company_name=asset.housing_company_name,
    )


async def _create_asset_from_listing(
    session: AsyncSession, listing: Listing
) -> PropertyAsset:
    """Create a new PropertyAsset from listing data."""
    blob = listing.json_blob or {}

    address = blob.get("address", blob.get("street_address", ""))
    if isinstance(address, dict):
        address = address.get("street", address.get("full", ""))

    postal_code = _extract_postal_code(listing) or ""
    municipality = blob.get("municipality", "")

    # Normalize the address for the canonical form
    canonical = normalize_finnish_address(address) if address else ""

    asset = PropertyAsset(
        asset_type=blob.get("asset_type", "unknown"),
        canonical_address=canonical or address or "unknown",
        postal_code=postal_code,
        municipality=municipality,
        lat=blob.get("lat"),
        lon=blob.get("lon"),
        parcel_id=blob.get("parcel_id"),
        building_id=blob.get("building_id"),
        housing_company_name=blob.get("housing_company_name"),
        source_confidence=0.5,
    )
    session.add(asset)
    await session.flush()  # Get the generated asset_id
    return asset


# ===================================================================
# 4. refresh_gold_views
# ===================================================================

_GOLD_VIEWS = [
    "property.latest_listing_state",
    "property.price_change_history",
    "property.market_velocity_by_postal_code",
]


async def refresh_gold_views() -> None:
    """Refresh materialized views in the gold layer.

    Attempts REFRESH MATERIALIZED VIEW CONCURRENTLY first (requires a
    unique index, which these views have).  Falls back to non-concurrent
    refresh if the concurrent refresh fails (e.g., first run before data
    exists).
    """
    engine = get_engine()

    for view_name in _GOLD_VIEWS:
        t0 = time.monotonic()
        refreshed = False

        # Try concurrent refresh first (requires unique index + data)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view_name}"
                    )
                )
            elapsed = time.monotonic() - t0
            logger.info(
                "Refreshed %s (concurrent) in %.2fs", view_name, elapsed
            )
            refreshed = True
        except Exception as exc:
            logger.warning(
                "Concurrent refresh failed for %s (%s), "
                "trying non-concurrent",
                view_name,
                exc,
            )

        # Fall back to non-concurrent refresh
        if not refreshed:
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(f"REFRESH MATERIALIZED VIEW {view_name}")
                    )
                elapsed = time.monotonic() - t0
                logger.info(
                    "Refreshed %s (non-concurrent) in %.2fs",
                    view_name,
                    elapsed,
                )
            except Exception:
                elapsed = time.monotonic() - t0
                logger.exception(
                    "Failed to refresh materialized view %s after %.2fs",
                    view_name,
                    elapsed,
                )
                # Continue with the next view — don't let one failure
                # block the rest.


# ===================================================================
# 5. run_enrichment_pipeline
# ===================================================================


async def run_enrichment_pipeline() -> dict[str, Any]:
    """Run the full enrichment pipeline in order.

    Pipeline stages:
      1. normalize  — bronze -> silver
      2. geocode    — address normalization + Nominatim geocoding
      3. resolve    — entity resolution (listing -> asset linkage)
      4. refresh    — gold materialized view refresh

    Returns a results dict with counts and timings.
    """
    results: dict[str, Any] = {}
    pipeline_start = time.monotonic()

    # Stage 1: Normalize
    stage_start = time.monotonic()
    try:
        results["normalized"] = await normalize_raw_snapshots()
    except Exception:
        logger.exception("normalize_raw_snapshots failed")
        results["normalized"] = 0
        results["normalize_error"] = True
    results["normalize_seconds"] = round(time.monotonic() - stage_start, 2)

    # Stage 2: Geocode / canonicalize addresses
    stage_start = time.monotonic()
    try:
        results["geocoded"] = await canonicalize_addresses()
    except Exception:
        logger.exception("canonicalize_addresses failed")
        results["geocoded"] = 0
        results["geocode_error"] = True
    results["geocode_seconds"] = round(time.monotonic() - stage_start, 2)

    # Stage 3: Entity resolution
    stage_start = time.monotonic()
    try:
        results["resolved"] = await run_entity_resolution()
    except Exception:
        logger.exception("run_entity_resolution failed")
        results["resolved"] = 0
        results["resolve_error"] = True
    results["resolve_seconds"] = round(time.monotonic() - stage_start, 2)

    # Stage 4: Refresh gold views
    stage_start = time.monotonic()
    try:
        await refresh_gold_views()
        results["views_refreshed"] = True
    except Exception:
        logger.exception("refresh_gold_views failed")
        results["views_refreshed"] = False
    results["refresh_seconds"] = round(time.monotonic() - stage_start, 2)

    results["total_seconds"] = round(time.monotonic() - pipeline_start, 2)
    return results


# ===================================================================
# Entry point
# ===================================================================


async def main() -> None:
    """Entry point for enrichment worker."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Property Intel enrichment worker starting")

    results = await run_enrichment_pipeline()
    logger.info("Enrichment complete: %s", results)


if __name__ == "__main__":
    asyncio.run(main())
