"""Area market endpoints for the Property Intelligence API.

All endpoints use real async SQLAlchemy queries against the property schema.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session
from jarvis_property_intel.models import AreaSnapshot, Listing, PropertyAsset
from jarvis_property_intel.schemas import (
    AreaHistoryResponse,
    AreaMarketSummary,
    AreaSnapshotItem,
)

router = APIRouter(prefix="/api/v1/property_intel/areas", tags=["areas"])

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


def _format_period(snapshot: AreaSnapshot) -> str:
    """Format period_start/period_end into a human-readable period string."""
    start = snapshot.period_start
    end = snapshot.period_end
    # Determine quarter from period_end month
    quarter = (end.month - 1) // 3 + 1
    return f"{end.year}-Q{quarter}"


@router.get(
    "/{postal_code}/market",
    response_model=AreaMarketSummary,
    summary="Area market summary",
    description=(
        "Return aggregated market KPIs for a postal-code area, "
        "optionally filtered by asset-type segment and period."
    ),
)
async def get_area_market(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str = Path(
        ..., min_length=3, max_length=10, description="Postal code"
    ),
    segment: str | None = Query(
        None, description="Asset-type segment filter (e.g. apartment, house)"
    ),
    period: str | None = Query(
        None,
        description="Period identifier (e.g. 2025-Q4). Defaults to latest.",
    ),
) -> AreaMarketSummary:
    """Return market KPIs for the requested postal-code area."""
    # Try to find a matching AreaSnapshot
    stmt = select(AreaSnapshot).where(
        AreaSnapshot.postal_code == postal_code,
    )

    if segment is not None:
        stmt = stmt.where(AreaSnapshot.segment == segment)
    else:
        stmt = stmt.where(AreaSnapshot.segment.is_(None))

    # If a specific period is requested, parse and filter
    if period is not None:
        # Period format: "YYYY-QN" -> filter by matching quarter
        try:
            year_str, q_str = period.split("-")
            year = int(year_str)
            quarter = int(q_str[1])
            # Quarter end months: Q1=3, Q2=6, Q3=9, Q4=12
            end_month = quarter * 3
            # Filter where period_end year and quarter match
            stmt = stmt.where(
                func.extract("year", AreaSnapshot.period_end) == year,
                func.extract("month", AreaSnapshot.period_end) == end_month,
            )
        except (ValueError, IndexError):
            # If period format is invalid, just use it as-is for matching
            pass

    # Get latest snapshot
    stmt = stmt.order_by(AreaSnapshot.period_end.desc()).limit(1)
    result = await session.execute(stmt)
    snapshot = result.scalar_one_or_none()

    if snapshot is not None:
        # Get municipality from snapshot
        return AreaMarketSummary(
            postal_code=snapshot.postal_code,
            municipality=snapshot.municipality,
            segment=snapshot.segment,
            period=_format_period(snapshot),
            median_ask_m2=snapshot.median_ask_m2,
            median_sold_m2=snapshot.median_sold_m2,
            dom_median=snapshot.dom_median,
            inventory_count=snapshot.inventory_count,
            price_cut_ratio=snapshot.price_cut_ratio,
            income_median=snapshot.income_median,
            owner_occupancy_ratio=snapshot.owner_occupancy_ratio,
        )

    # No snapshot found -- compute from live listing data
    base_filter = [
        PropertyAsset.postal_code == postal_code,
    ]
    if segment is not None:
        base_filter.append(PropertyAsset.asset_type == segment)

    # Count active listings
    count_stmt = (
        select(func.count())
        .select_from(Listing)
        .join(
            PropertyAsset,
            Listing.asset_id == PropertyAsset.asset_id,
        )
        .where(Listing.status == "active", *base_filter)
    )
    inventory_count = (await session.execute(count_stmt)).scalar_one()

    if inventory_count == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No market data found for postal code {postal_code}"
                + (f" segment={segment}" if segment else "")
            ),
        )

    # Compute median asking price per m2
    price_m2_stmt = (
        select(
            Listing.asking_price / Listing.living_area_m2,
        )
        .join(
            PropertyAsset,
            Listing.asset_id == PropertyAsset.asset_id,
        )
        .where(
            Listing.status == "active",
            Listing.asking_price.isnot(None),
            Listing.living_area_m2.isnot(None),
            Listing.living_area_m2 > 0,
            *base_filter,
        )
        .order_by(Listing.asking_price / Listing.living_area_m2)
    )
    price_m2_result = await session.execute(price_m2_stmt)
    price_m2_values = [row[0] for row in price_m2_result.all()]

    median_ask_m2: float | None = None
    if price_m2_values:
        mid = len(price_m2_values) // 2
        if len(price_m2_values) % 2 == 0:
            median_ask_m2 = round(
                (price_m2_values[mid - 1] + price_m2_values[mid]) / 2, 2
            )
        else:
            median_ask_m2 = round(price_m2_values[mid], 2)

    # Get municipality from any property in this postal code
    muni_stmt = (
        select(PropertyAsset.municipality)
        .where(PropertyAsset.postal_code == postal_code)
        .limit(1)
    )
    muni_result = await session.execute(muni_stmt)
    municipality = muni_result.scalar_one_or_none()

    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    period_str = period or f"{today.year}-Q{quarter}"

    return AreaMarketSummary(
        postal_code=postal_code,
        municipality=municipality,
        segment=segment,
        period=period_str,
        median_ask_m2=median_ask_m2,
        median_sold_m2=None,  # No transaction data in live computation
        dom_median=None,
        inventory_count=inventory_count,
        price_cut_ratio=None,
        income_median=None,
        owner_occupancy_ratio=None,
    )


@router.get(
    "/{postal_code}/history",
    response_model=AreaHistoryResponse,
    summary="Area price history",
    description=(
        "Return a time series of area market snapshots ordered by period, "
        "useful for charting price trends over time."
    ),
)
async def get_area_history(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str = Path(
        ..., min_length=3, max_length=10, description="Postal code"
    ),
    segment: str | None = Query(
        None, description="Asset-type segment filter (e.g. apartment, house)"
    ),
    limit: int = Query(20, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> AreaHistoryResponse:
    """Return historical area snapshots as a time series."""
    stmt = select(AreaSnapshot).where(
        AreaSnapshot.postal_code == postal_code,
    )

    if segment is not None:
        stmt = stmt.where(AreaSnapshot.segment == segment)
    else:
        stmt = stmt.where(AreaSnapshot.segment.is_(None))

    stmt = (
        stmt.order_by(AreaSnapshot.period_end.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await session.execute(stmt)
    snapshots = result.scalars().all()

    return AreaHistoryResponse(
        postal_code=postal_code,
        snapshots=[
            AreaSnapshotItem(
                snapshot_id=s.snapshot_id,
                postal_code=s.postal_code,
                municipality=s.municipality,
                period_start=s.period_start,
                period_end=s.period_end,
                segment=s.segment,
                median_ask_m2=s.median_ask_m2,
                median_sold_m2=s.median_sold_m2,
                dom_median=s.dom_median,
                inventory_count=s.inventory_count,
                price_cut_ratio=s.price_cut_ratio,
                income_median=s.income_median,
                owner_occupancy_ratio=s.owner_occupancy_ratio,
            )
            for s in snapshots
        ],
    )
