"""Transaction endpoints — realized sale prices from KVKL."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/property_intel/transactions", tags=["transactions"])

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


@router.get("")
async def list_transactions(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    city: str | None = Query(None),
    building_type: str | None = Query(None),
    min_price: float | None = Query(None),
    max_price: float | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List realized sale transactions with optional filters."""
    conditions = ["source = 'hintatiedot_kvkl'", "transaction_price > 0"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if city:
        conditions.append("LOWER(municipality) = LOWER(:city)")
        params["city"] = city
    if building_type:
        conditions.append("building_type = :building_type")
        params["building_type"] = building_type
    if min_price:
        conditions.append("transaction_price >= :min_price")
        params["min_price"] = min_price
    if max_price:
        conditions.append("transaction_price <= :max_price")
        params["max_price"] = max_price

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT transaction_id, municipality, neighborhood, building_type,
               living_area_m2, transaction_price, price_per_m2, year_built,
               room_config, floor, elevator, condition, lot_type, energy_class,
               transaction_date, fetched_at
        FROM property.transaction
        WHERE {where}
        ORDER BY transaction_price DESC
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/stats")
async def transaction_stats(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    city: str | None = Query(None),
    since_days: int = Query(90, description="Only include transactions first seen within this many days"),
) -> list[dict[str, Any]]:
    """Aggregate transaction statistics per municipality.

    Default: last 90 days of data. Use since_days=0 for all-time.
    """
    conditions = ["source = 'hintatiedot_kvkl'", "transaction_price > 0", "price_per_m2 > 0"]
    params: dict[str, Any] = {}

    if since_days > 0:
        conditions.append("first_seen_at >= NOW() - make_interval(days => :since_days)")
        params["since_days"] = since_days

    if city:
        conditions.append("LOWER(municipality) = LOWER(:city)")
        params["city"] = city

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT municipality,
            COUNT(*) AS transactions,
            ROUND(AVG(transaction_price)::numeric) AS avg_price,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY transaction_price)::numeric) AS median_price,
            ROUND(AVG(price_per_m2)::numeric) AS avg_m2,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_per_m2)::numeric) AS median_m2,
            ROUND(AVG(living_area_m2)::numeric, 1) AS avg_area,
            MIN(first_seen_at)::date AS earliest,
            MAX(first_seen_at)::date AS latest
        FROM property.transaction
        WHERE {where}
        GROUP BY municipality
        ORDER BY transactions DESC
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]
