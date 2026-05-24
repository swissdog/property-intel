"""Analytics endpoints — price gap, market velocity, backup export."""

from __future__ import annotations

import csv
import io
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/property_intel", tags=["analytics"])

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


@router.get("/price-gap")
async def price_gap(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    """Asking price vs realized sale price per municipality."""
    sql = text("SELECT * FROM property.price_gap_by_municipality ORDER BY active_listings DESC")
    result = await session.execute(sql)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/velocity")
async def market_velocity(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None),
    weeks: int = Query(4, ge=1, le=52),
) -> list[dict[str, Any]]:
    """Market velocity (new/removed listings, DOM) per postal code per week."""
    conditions = ["1=1"]
    row_limit = weeks * 100
    params: dict[str, Any] = {"row_limit": row_limit}

    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT postal_code, week_start, week_end, active_count,
               median_asking_price, median_dom, new_listings, removed_listings
        FROM property.market_velocity_by_postal_code
        WHERE {where}
        ORDER BY week_start DESC
        LIMIT :row_limit
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/export/backup")
async def export_backup(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    format: str = Query("csv", pattern="^(csv|json)$"),
) -> StreamingResponse:
    """Full database export for JARVIS backup (cloud -> local, one-way).

    Returns all tables as CSV or JSON for JARVIS to import into local PostGIS.
    """
    tables = [
        "property.property_asset",
        "property.listing",
        "property.listing_event",
        "property.area_snapshot",
        "property.transaction",
    ]

    if format == "json":
        import json

        from jarvis_property_intel.db import session_scope

        async def json_stream():
            yield '{"tables":{'
            first_table = True
            async with session_scope() as session:
                for tbl in tables:
                    if not first_table:
                        yield ","
                    first_table = False
                    name = tbl.split(".")[-1]
                    result = await session.execute(text(f"SELECT * FROM {tbl}"))
                    rows = [dict(r._mapping) for r in result.fetchall()]
                    yield f'"{name}":{json.dumps(rows, default=str)}'
            yield "}}"

        return StreamingResponse(
            json_stream(),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=property_backup.json"},
        )

    # CSV format: one CSV per table, separated by header markers
    from jarvis_property_intel.db import session_scope

    async def csv_stream():
        async with session_scope() as session:
            for tbl in tables:
                name = tbl.split(".")[-1]
                yield f"--- TABLE: {name} ---\n"
                result = await session.execute(text(f"SELECT * FROM {tbl}"))
                rows = result.fetchall()
                if not rows:
                    yield "--- EMPTY ---\n"
                    continue
                keys = list(rows[0]._mapping.keys())
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(keys)
                yield buf.getvalue()
                for row in rows:
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    writer.writerow([str(v) if v is not None else "" for v in row._mapping.values()])
                    yield buf.getvalue()

    return StreamingResponse(
        csv_stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=property_backup.csv"},
    )
