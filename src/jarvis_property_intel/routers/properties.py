"""Property endpoints for the Property Intelligence API.

All endpoints use real async SQLAlchemy queries against the property schema.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session
from jarvis_property_intel.models import (
    AreaSnapshot,
    BuildingFeatures,
    Listing,
    ListingEvent,
    PropertyAsset,
    Transaction,
)
from jarvis_property_intel.schemas import (
    BuildingFeaturesDetail,
    ComparableProperty,
    ComparablesResponse,
    LatestListingSummary,
    ListingDetail,
    ListingEventItem,
    PaginatedPropertyResponse,
    PropertyDetail,
    PropertySummary,
    TimelineResponse,
    TransactionDetail,
    ValuationResponse,
)

router = APIRouter(prefix="/api/v1/property_intel/properties", tags=["properties"])

# Approximate degrees-per-km at mid-latitudes (Finland ~60-70N)
_DEG_PER_KM_LAT = 1.0 / 111.0
_DEG_PER_KM_LON_60N = 1.0 / 55.8  # cos(60) * 111 ≈ 55.8

_verify_dep = None


def _get_verify():
    global _verify_dep
    if _verify_dep is None:
        s = get_settings()
        _verify_dep = verify_module_auth_dependency(
            secret=s.x_module_auth_secret,
            expected_module="property_intel",
        )
    return _verify_dep


# ---------------------------------------------------------------------------
# Helper mappers
# ---------------------------------------------------------------------------


def _listing_to_summary(listing: Listing) -> LatestListingSummary:
    return LatestListingSummary(
        listing_id=listing.listing_id,
        source=listing.source,
        source_listing_id=listing.source_listing_id,
        status=listing.status,
        asking_price=listing.asking_price,
        living_area_m2=listing.living_area_m2,
        first_seen_at=listing.first_seen_at,
        last_seen_at=listing.last_seen_at,
    )


def _asset_to_summary(
    asset: PropertyAsset, latest_listing: Listing | None = None
) -> PropertySummary:
    return PropertySummary(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        canonical_address=asset.canonical_address,
        postal_code=asset.postal_code,
        municipality=asset.municipality,
        lat=asset.lat,
        lon=asset.lon,
        source_confidence=asset.source_confidence,
        latest_listing=(
            _listing_to_summary(latest_listing) if latest_listing else None
        ),
    )


def _listing_to_detail(listing: Listing) -> ListingDetail:
    return ListingDetail(
        listing_id=listing.listing_id,
        source=listing.source,
        source_listing_id=listing.source_listing_id,
        first_seen_at=listing.first_seen_at,
        last_seen_at=listing.last_seen_at,
        status=listing.status,
        asking_price=listing.asking_price,
        living_area_m2=listing.living_area_m2,
        year_built=listing.year_built,
        rooms=listing.rooms,
        lot_area_m2=listing.lot_area_m2,
        energy_class=listing.energy_class,
    )


def _transaction_to_detail(txn: Transaction) -> TransactionDetail:
    return TransactionDetail(
        transaction_id=txn.transaction_id,
        transaction_date=txn.transaction_date,
        sale_date=txn.sale_date,
        sale_date_precision=txn.sale_date_precision,
        transaction_price=txn.transaction_price,
        transaction_type=txn.transaction_type,
        source=txn.source,
    )


def _features_to_detail(
    bf: BuildingFeatures | None,
) -> BuildingFeaturesDetail | None:
    if bf is None:
        return None
    return BuildingFeaturesDetail(
        heating_type=bf.heating_type,
        sauna=bf.sauna,
        garage=bf.garage,
        waterfront_proxy=bf.waterfront_proxy,
        school_distance_m=bf.school_distance_m,
        elevation=bf.elevation,
        transit_score_proxy=bf.transit_score_proxy,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/search",
    response_model=PaginatedPropertyResponse,
    summary="Search properties",
    description=(
        "Search for properties by location, postal code, municipality, "
        "or asset type.  Supports radius-based geo queries when lat/lon "
        "are provided."
    ),
)
async def search_properties(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, description="Filter by postal code"),
    municipality: str | None = Query(
        None, description="Filter by municipality name"
    ),
    lat: float | None = Query(None, description="Latitude for geo search"),
    lon: float | None = Query(None, description="Longitude for geo search"),
    radius_km: float | None = Query(
        5.0, ge=0.1, le=100.0, description="Search radius in km"
    ),
    asset_type: str | None = Query(
        None, description="Filter by asset type (apartment, house, etc.)"
    ),
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> PaginatedPropertyResponse:
    """Search properties with optional geo, postal-code, and type filters."""
    # Build base query
    stmt = select(PropertyAsset)
    count_stmt = select(func.count()).select_from(PropertyAsset)

    # Apply filters
    if postal_code is not None:
        stmt = stmt.where(PropertyAsset.postal_code == postal_code)
        count_stmt = count_stmt.where(
            PropertyAsset.postal_code == postal_code
        )

    if municipality is not None:
        stmt = stmt.where(
            func.lower(PropertyAsset.municipality)
            == func.lower(municipality)
        )
        count_stmt = count_stmt.where(
            func.lower(PropertyAsset.municipality)
            == func.lower(municipality)
        )

    if asset_type is not None:
        stmt = stmt.where(PropertyAsset.asset_type == asset_type)
        count_stmt = count_stmt.where(
            PropertyAsset.asset_type == asset_type
        )

    # Geo bounding-box filter (degree approximation)
    if lat is not None and lon is not None and radius_km is not None:
        dlat = radius_km * _DEG_PER_KM_LAT
        dlon = radius_km * _DEG_PER_KM_LON_60N
        geo_filters = [
            PropertyAsset.lat.isnot(None),
            PropertyAsset.lon.isnot(None),
            PropertyAsset.lat >= lat - dlat,
            PropertyAsset.lat <= lat + dlat,
            PropertyAsset.lon >= lon - dlon,
            PropertyAsset.lon <= lon + dlon,
        ]
        for f in geo_filters:
            stmt = stmt.where(f)
            count_stmt = count_stmt.where(f)

    # Get total count
    total = (await session.execute(count_stmt)).scalar_one()

    # Apply pagination and ordering
    stmt = (
        stmt.order_by(PropertyAsset.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await session.execute(stmt)
    assets = result.scalars().all()

    # Batch fetch latest active listing for all assets (single query, not N+1)
    asset_ids = [a.asset_id for a in assets]
    latest_listings_map: dict = {}
    if asset_ids:
        # Window function: rank listings by asset_id, pick latest
        ranked = (
            select(
                Listing,
                func.row_number().over(
                    partition_by=Listing.asset_id,
                    order_by=Listing.last_seen_at.desc(),
                ).label("rn"),
            )
            .where(
                Listing.asset_id.in_(asset_ids),
                Listing.status == "active",
            )
            .subquery()
        )
        latest_stmt = select(Listing).from_statement(
            select(ranked).where(ranked.c.rn == 1)
        )
        latest_result = await session.execute(latest_stmt)
        for listing in latest_result.scalars().all():
            latest_listings_map[listing.asset_id] = listing

    items: list[PropertySummary] = []
    for asset in assets:
        latest_listing = latest_listings_map.get(asset.asset_id)
        items.append(_asset_to_summary(asset, latest_listing))

    return PaginatedPropertyResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{asset_id}",
    response_model=PropertyDetail,
    summary="Get property details",
    description=(
        "Retrieve full property details including listing history, "
        "matched transactions, and building features."
    ),
)
async def get_property(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    asset_id: uuid.UUID = Path(..., description="Property asset UUID"),
) -> PropertyDetail:
    """Return full property asset with related listings and transactions."""
    stmt = (
        select(PropertyAsset)
        .where(PropertyAsset.asset_id == asset_id)
        .options(
            selectinload(PropertyAsset.listings),
            selectinload(PropertyAsset.transactions),
            selectinload(PropertyAsset.building_features),
        )
    )
    result = await session.execute(stmt)
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Property asset {asset_id} not found",
        )

    # Sort listings by last_seen_at desc
    sorted_listings = sorted(
        asset.listings, key=lambda l: l.last_seen_at, reverse=True
    )
    # Sort transactions by real sale recency: known sale_date first (most recent
    # first), unknown-date rows after — never let the ingest date masquerade as
    # the sale date in ordering. Falls back to transaction_date as tiebreaker.
    sorted_txns = sorted(
        asset.transactions,
        key=lambda t: (t.sale_date is not None, t.sale_date or t.transaction_date),
        reverse=True,
    )

    return PropertyDetail(
        asset_id=asset.asset_id,
        asset_type=asset.asset_type,
        canonical_address=asset.canonical_address,
        postal_code=asset.postal_code,
        municipality=asset.municipality,
        lat=asset.lat,
        lon=asset.lon,
        parcel_id=asset.parcel_id,
        building_id=asset.building_id,
        housing_company_name=asset.housing_company_name,
        source_confidence=asset.source_confidence,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        building_features=_features_to_detail(asset.building_features),
        listings=[_listing_to_detail(l) for l in sorted_listings],
        transactions=[_transaction_to_detail(t) for t in sorted_txns],
    )


@router.get(
    "/{asset_id}/timeline",
    response_model=TimelineResponse,
    summary="Property listing timeline",
    description=(
        "Return an ordered list of listing events (price changes, "
        "status updates, etc.) for a given property."
    ),
)
async def get_property_timeline(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    asset_id: uuid.UUID = Path(..., description="Property asset UUID"),
) -> TimelineResponse:
    """Return listing events ordered by event_at for the given asset."""
    # Verify asset exists
    asset_exists = (
        await session.execute(
            select(PropertyAsset.asset_id).where(
                PropertyAsset.asset_id == asset_id
            )
        )
    ).scalar_one_or_none()

    if asset_exists is None:
        raise HTTPException(
            status_code=404,
            detail=f"Property asset {asset_id} not found",
        )

    # Query listing events via listing join
    stmt = (
        select(ListingEvent)
        .join(Listing, ListingEvent.listing_id == Listing.listing_id)
        .where(Listing.asset_id == asset_id)
        .order_by(ListingEvent.event_at.desc())
    )
    result = await session.execute(stmt)
    events = result.scalars().all()

    return TimelineResponse(
        asset_id=asset_id,
        events=[
            ListingEventItem(
                event_id=e.event_id,
                listing_id=e.listing_id,
                event_type=e.event_type,
                event_at=e.event_at,
                old_value=e.old_value,
                new_value=e.new_value,
            )
            for e in events
        ],
    )


@router.get(
    "/{asset_id}/comparables",
    response_model=ComparablesResponse,
    summary="Find comparable properties",
    description=(
        "Find properties comparable to the given asset based on location, "
        "size, type, and other features.  Each comparable includes a "
        "similarity score between 0 and 1."
    ),
)
async def get_comparables(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    asset_id: uuid.UUID = Path(..., description="Property asset UUID"),
    limit: int = Query(
        10, ge=1, le=50, description="Max comparables to return"
    ),
) -> ComparablesResponse:
    """Return comparable properties with similarity scores."""
    # Fetch the reference asset with its latest active listing
    ref_stmt = select(PropertyAsset).where(
        PropertyAsset.asset_id == asset_id
    )
    ref_result = await session.execute(ref_stmt)
    ref_asset = ref_result.scalar_one_or_none()

    if ref_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Property asset {asset_id} not found",
        )

    # Get reference listing data (latest active or any latest)
    ref_listing_stmt = (
        select(Listing)
        .where(Listing.asset_id == asset_id)
        .order_by(Listing.last_seen_at.desc())
        .limit(1)
    )
    ref_listing_result = await session.execute(ref_listing_stmt)
    ref_listing = ref_listing_result.scalar_one_or_none()

    ref_area = ref_listing.living_area_m2 if ref_listing else None
    ref_year = ref_listing.year_built if ref_listing else None

    # Build comparable query: same postal_code, same asset_type, exclude self
    comp_stmt = (
        select(PropertyAsset, Listing)
        .outerjoin(
            Listing,
            (Listing.asset_id == PropertyAsset.asset_id)
            & (Listing.status == "active"),
        )
        .where(
            PropertyAsset.postal_code == ref_asset.postal_code,
            PropertyAsset.asset_type == ref_asset.asset_type,
            PropertyAsset.asset_id != asset_id,
        )
    )

    # Filter by similar living_area_m2 (+-20%) if we have reference data
    if ref_area is not None:
        comp_stmt = comp_stmt.where(
            Listing.living_area_m2 >= ref_area * 0.8,
            Listing.living_area_m2 <= ref_area * 1.2,
        )

    # Filter by similar year_built (+-10 years) if we have reference data
    if ref_year is not None:
        comp_stmt = comp_stmt.where(
            Listing.year_built >= ref_year - 10,
            Listing.year_built <= ref_year + 10,
        )

    result = await session.execute(comp_stmt)
    rows = result.all()

    # Compute similarity scores and build response
    comparables: list[ComparableProperty] = []
    for comp_asset, comp_listing in rows:
        # Similarity score: combine area similarity and year similarity
        score_components: list[float] = []

        comp_area = comp_listing.living_area_m2 if comp_listing else None
        comp_year = comp_listing.year_built if comp_listing else None

        if ref_area and comp_area:
            area_ratio = min(ref_area, comp_area) / max(ref_area, comp_area)
            score_components.append(area_ratio)

        if ref_year and comp_year:
            year_diff = abs(ref_year - comp_year)
            year_score = max(0.0, 1.0 - year_diff / 20.0)
            score_components.append(year_score)

        # Geo distance component
        distance_km: float | None = None
        if (
            ref_asset.lat is not None
            and ref_asset.lon is not None
            and comp_asset.lat is not None
            and comp_asset.lon is not None
        ):
            dlat = ref_asset.lat - comp_asset.lat
            dlon = ref_asset.lon - comp_asset.lon
            # Haversine approximation for short distances
            distance_km = math.sqrt(
                (dlat / _DEG_PER_KM_LAT) ** 2
                + (dlon / _DEG_PER_KM_LON_60N) ** 2
            )
            # Distance score: 1.0 at 0km, 0.0 at 5km+
            dist_score = max(0.0, 1.0 - distance_km / 5.0)
            score_components.append(dist_score)

        similarity = (
            sum(score_components) / len(score_components)
            if score_components
            else 0.5
        )
        similarity = round(min(1.0, max(0.0, similarity)), 3)

        comparables.append(
            ComparableProperty(
                asset_id=comp_asset.asset_id,
                canonical_address=comp_asset.canonical_address,
                postal_code=comp_asset.postal_code,
                municipality=comp_asset.municipality,
                asset_type=comp_asset.asset_type,
                asking_price=(
                    comp_listing.asking_price if comp_listing else None
                ),
                living_area_m2=comp_area,
                year_built=comp_year,
                distance_km=(
                    round(distance_km, 3) if distance_km is not None else None
                ),
                similarity_score=similarity,
            )
        )

    # Sort by similarity descending, take top N
    comparables.sort(key=lambda c: c.similarity_score, reverse=True)
    comparables = comparables[:limit]

    return ComparablesResponse(
        asset_id=asset_id, comparables=comparables
    )


@router.get(
    "/{asset_id}/valuation",
    response_model=ValuationResponse,
    summary="Estimate property valuation",
    description=(
        "Return an automated valuation estimate with low, fair, and high "
        "bounds plus a confidence score based on area market data."
    ),
)
async def get_valuation(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    asset_id: uuid.UUID = Path(..., description="Property asset UUID"),
) -> ValuationResponse:
    """Return valuation estimate based on area snapshot data."""
    # Fetch asset
    asset_stmt = select(PropertyAsset).where(
        PropertyAsset.asset_id == asset_id
    )
    asset_result = await session.execute(asset_stmt)
    asset = asset_result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Property asset {asset_id} not found",
        )

    # Get living_area_m2 from the latest listing
    listing_stmt = (
        select(Listing)
        .where(Listing.asset_id == asset_id)
        .order_by(Listing.last_seen_at.desc())
        .limit(1)
    )
    listing_result = await session.execute(listing_stmt)
    listing = listing_result.scalar_one_or_none()

    living_area = listing.living_area_m2 if listing else None

    # Get latest area snapshot with median_sold_m2
    snapshot_stmt = (
        select(AreaSnapshot)
        .where(
            AreaSnapshot.postal_code == asset.postal_code,
            AreaSnapshot.median_sold_m2.isnot(None),
        )
        .order_by(AreaSnapshot.period_end.desc())
        .limit(1)
    )
    snapshot_result = await session.execute(snapshot_stmt)
    snapshot = snapshot_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if snapshot is not None and living_area is not None:
        fair = snapshot.median_sold_m2 * living_area
        low = fair * 0.85
        high = fair * 1.15
        # Confidence based on data availability
        confidence = 0.6
        if listing and listing.asking_price:
            confidence = 0.7
        model_version = "area-snapshot-v1"
    elif living_area is not None:
        # No area data, use asking price as basis if available
        if listing and listing.asking_price:
            fair = listing.asking_price
            low = fair * 0.85
            high = fair * 1.15
            confidence = 0.3
        else:
            fair = 0.0
            low = 0.0
            high = 0.0
            confidence = 0.0
        model_version = "listing-fallback-v1"
    else:
        # No data at all
        fair = 0.0
        low = 0.0
        high = 0.0
        confidence = 0.0
        model_version = "no-data-v0"

    return ValuationResponse(
        asset_id=asset_id,
        low=round(low, 2),
        fair=round(fair, 2),
        high=round(high, 2),
        confidence=confidence,
        model_version=model_version,
        computed_at=now,
    )
