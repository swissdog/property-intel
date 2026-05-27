"""Intelligence endpoints — analyst-grade derived data product.

These endpoints serve the denormalized analytical views built on top of
listings, transactions, rents, construction, migration, and rates. The
shape of each response is the contract a paying customer relies on, so
keep additive-only changes here.

All views live in property.* schema:
- v_postal_investor_lens         -> /investor-lens
- v_yield_anomalies              -> /yield-anomalies
- v_market_velocity_timeseries   -> /market-velocity (extended view, vs the
                                   weekly one already in /api/v1/velocity)
- v_supply_demand                -> /supply-demand
- v_national_headline            -> /national-headline

Plus raw access to:
- property.interest_rate         -> /rates
- property.rent_snapshot         -> /rents
- property.migration_activity    -> /migration
- property.construction_activity -> /construction
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/property_intel/intel", tags=["intelligence"])

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
# Postal-code level
# ---------------------------------------------------------------------------

@router.get("/investor-lens")
async def investor_lens(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
    municipality_code: str | None = Query(None, max_length=10),
    min_yield_pct: float | None = Query(None, ge=0, le=50),
    max_price_m2: float | None = Query(None, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Postal-code investor lens: latest yield, sold price, rent, 5y growth."""
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    if municipality_code:
        conditions.append("municipality_code = :muni")
        params["muni"] = municipality_code
    if min_yield_pct is not None:
        conditions.append("gross_yield_pct >= :min_yield")
        params["min_yield"] = min_yield_pct
    if max_price_m2 is not None:
        conditions.append("median_sold_m2 <= :max_price")
        params["max_price"] = max_price_m2

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT * FROM property.v_postal_investor_lens
        WHERE {where}
        ORDER BY gross_yield_pct DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/yield-anomalies")
async def yield_anomalies(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Postal codes with above-median yield AND below-median price (deal lens)."""
    sql = text(
        "SELECT * FROM property.v_yield_anomalies LIMIT :limit"
    )
    result = await session.execute(sql, {"limit": limit})
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/market-velocity")
async def market_velocity_extended(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
    quarters_back: int = Query(20, ge=1, le=80),
) -> list[dict[str, Any]]:
    """Quarterly listings, DOM, removed-vs-active per postal code."""
    conditions = ["1=1"]
    params: dict[str, Any] = {"quarters_back": quarters_back}
    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT * FROM property.v_market_velocity_timeseries
        WHERE {where}
        ORDER BY postal_code, quarter_start DESC
        LIMIT :quarters_back * 100
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# National / regional
# ---------------------------------------------------------------------------

@router.get("/national-headline")
async def national_headline(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    quarters_back: int = Query(24, ge=1, le=80),
) -> list[dict[str, Any]]:
    """Quarterly Finland-wide avg price, rent, Euribor 12M, yield, completions."""
    sql = text(
        "SELECT * FROM property.v_national_headline LIMIT :n"
    )
    result = await session.execute(sql, {"n": quarters_back})
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/postal-codes/top")
async def top_postal_codes(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Top-N postinumerot aktiivisten listausten määrän mukaan.

    Käytetään Property Intel -sivun TypeAhead-pohjaehdotuksina (Story 4.2);
    free-text 5-numero ohittaa tämän listan UI-puolella.
    """
    sql = text(
        """
        SELECT
            pa.postal_code,
            COALESCE(pca.name, '') AS area_name,
            COALESCE(pca.municipality_name, pa.municipality, '') AS municipality,
            count(l.listing_id)::int AS listing_count
        FROM property.property_asset pa
        JOIN property.listing l ON l.asset_id = pa.asset_id
        LEFT JOIN property.postal_code_area pca ON pca.postal_code = pa.postal_code
        WHERE pa.postal_code IS NOT NULL
          AND pa.postal_code ~ '^[0-9]{5}$'
          AND l.status = 'active'
        GROUP BY pa.postal_code, pca.name, pca.municipality_name, pa.municipality
        ORDER BY listing_count DESC, pa.postal_code ASC
        LIMIT :n
        """
    )
    result = await session.execute(sql, {"n": limit})
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/supply-demand")
async def supply_demand(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    region_code: str | None = Query(None, max_length=10),
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
) -> list[dict[str, Any]]:
    """Annual supply (dwellings completed) vs demand (net migration) per region.

    Accepts either region_code directly (e.g. "06" = Pirkanmaa) or a postal
    code which is resolved -> municipality -> region via postal_code_area +
    municipality_region. UI calls with postal_code so the panel surfaces the
    correct maakunta automatically.
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {}
    if region_code:
        conditions.append("v.region_code = :region_code")
        params["region_code"] = region_code
    if postal_code:
        # Resolve postal_code -> region_code via postal_code_area ->
        # municipality_region. Note: v_supply_demand uses 'MK01..MK21' prefixed
        # codes (StatFi maakunta classification), while municipality_region
        # stores '01..21' bare. Prepend 'MK' to bridge.
        conditions.append(
            """v.region_code = 'MK' || (
                SELECT mr.region_code
                FROM property.postal_code_area pca
                JOIN property.municipality_region mr
                  ON mr.municipality_code = pca.municipality_code
                WHERE pca.postal_code = :postal_code
                LIMIT 1
            )"""
        )
        params["postal_code"] = postal_code
    where = " AND ".join(conditions)
    # Order so the snapshot panel's first row is the most-recent year with a
    # non-null ratio. yr DESC alone would lead with 2026 projections that have
    # demand=null. Forecast years stay in the result for trend graphs that want
    # them, but at the tail.
    sql = text(
        f"""
        SELECT v.* FROM property.v_supply_demand v
        WHERE {where}
        ORDER BY
          (v.demand_supply_ratio_pct IS NOT NULL) DESC,
          v.yr DESC,
          v.region_code
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Raw time series
# ---------------------------------------------------------------------------

@router.get("/rates")
async def interest_rates(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    rate_type: str | None = Query(None, max_length=40),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    limit: int = Query(2000, ge=1, le=10000),
) -> list[dict[str, Any]]:
    """ECB / Euribor rate observations.

    rate_type: 'euribor_1m', 'euribor_3m', 'euribor_6m', 'euribor_12m',
               'ecb_mro', 'ecb_dfr'. Omit for all.
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if rate_type:
        conditions.append("rate_type = :rate_type")
        params["rate_type"] = rate_type
    if from_date:
        conditions.append("observation_date >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("observation_date <= :to_date")
        params["to_date"] = to_date

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT rate_type, observation_date, frequency, value_pct, source_series
        FROM property.interest_rate
        WHERE {where}
        ORDER BY rate_type, observation_date DESC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/rents")
async def rents(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
    room_count_band: str | None = Query(None, pattern=r"^(1h|2h|3h\+)$"),
    from_year: int | None = Query(None, ge=2015, le=2030),
    limit: int = Query(2000, ge=1, le=20000),
) -> list[dict[str, Any]]:
    """Postal-code level rent history (StatFi 13eb), 2015Q1+ when available."""
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    if room_count_band:
        conditions.append("room_count_band = :rcb")
        params["rcb"] = room_count_band
    if from_year:
        conditions.append("EXTRACT(YEAR FROM period_start) >= :from_year")
        params["from_year"] = from_year

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT postal_code, period_start, period_end, room_count_band,
               median_rent_per_m2, rental_contract_count
        FROM property.rent_snapshot
        WHERE {where}
        ORDER BY postal_code, period_start DESC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/migration")
async def migration(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    municipality_code: str | None = Query(None, max_length=8),
    from_year: int | None = Query(None, ge=1990, le=2030),
    limit: int = Query(2000, ge=1, le=20000),
) -> list[dict[str, Any]]:
    """Annual municipal net migration (StatFi 11ae)."""
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if municipality_code:
        conditions.append("municipality_code = :mc")
        params["mc"] = municipality_code
    if from_year:
        conditions.append("period_year >= :fy")
        params["fy"] = from_year

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT * FROM property.migration_activity
        WHERE {where}
        ORDER BY municipality_code, period_year DESC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/construction")
async def construction(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    region_code: str | None = Query(None, max_length=10),
    phase: str | None = Query(None, pattern=r"^(permit|start|completion)$"),
    building_class_code: str | None = Query(None, max_length=10),
    from_year: int | None = Query(None, ge=1995, le=2030),
    limit: int = Query(5000, ge=1, le=50000),
) -> list[dict[str, Any]]:
    """Monthly construction activity per region (StatFi 156f)."""
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if region_code:
        conditions.append("region_code = :rc")
        params["rc"] = region_code
    if phase:
        conditions.append("phase = :phase")
        params["phase"] = phase
    if building_class_code:
        conditions.append("building_class_code = :bcc")
        params["bcc"] = building_class_code
    if from_year:
        conditions.append("EXTRACT(YEAR FROM period_start) >= :fy")
        params["fy"] = from_year

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT region_code, period_year_month, period_start, phase, phase_code,
               building_class_code, new_dwellings, floor_area_m2, volume_m3, activity_count
        FROM property.construction_activity
        WHERE {where}
        ORDER BY region_code, period_start DESC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Demographics & macro market (Paavo + Suomen Pankki)
# ---------------------------------------------------------------------------

@router.get("/demographics")
async def demographics(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
    municipality_code: str | None = Query(None, max_length=10),
    min_population: int | None = Query(None, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Per postal-code demographic profile (Paavo: population, income,
    education, employment, housing-stock composition).

    Latest year is used (per-pc DISTINCT ON year DESC in the view).
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    if municipality_code:
        conditions.append("municipality_code = :muni")
        params["muni"] = municipality_code
    if min_population is not None:
        conditions.append("population_total >= :min_pop")
        params["min_pop"] = min_population

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT * FROM property.v_postal_demographics
        WHERE {where}
        ORDER BY population_total DESC NULLS LAST
        LIMIT :limit OFFSET :offset
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/mortgage-market")
async def mortgage_market(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    limit: int = Query(240, ge=1, le=2000),
) -> list[dict[str, Any]]:
    """Monthly Finnish mortgage-market summary (BoF + ECB).

    Returns one row per month with avg_rate_new_loans_pct,
    avg_rate_outstanding_pct, euribor_12m_pct, implied_margin_pp,
    new_loans_volume_meur, housing_loan_stock_meur. Most recent first.
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if from_date:
        conditions.append("period >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("period <= :to_date")
        params["to_date"] = to_date

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT period, avg_rate_new_loans_pct, avg_rate_outstanding_pct,
               euribor_12m_pct, implied_margin_pp,
               new_loans_volume_meur, housing_loan_stock_meur
        FROM property.v_mortgage_market
        WHERE {where}
        ORDER BY period DESC
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/flood-risk")
async def flood_risk(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    postal_code: str | None = Query(None, pattern=r"^\d{5}$"),
    scenario: str | None = Query(None, pattern=r"^(100y|250y|significant)$"),
    limit: int = Query(1000, ge=1, le=10000),
) -> list[dict[str, Any]]:
    """Per postal-code flood-risk overlap (SYKE INSPIRE flood maps).

    Returns rows where the postal-code polygon intersects a SYKE flood-risk
    polygon. ``scenario`` filters to a single risk class.
    """
    conditions: list[str] = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if postal_code:
        conditions.append("postal_code = :postal_code")
        params["postal_code"] = postal_code
    if scenario:
        conditions.append("scenario = :scenario")
        params["scenario"] = scenario

    where = " AND ".join(conditions)
    sql = text(
        f"""
        SELECT postal_code, scenario, area_name, municipality_code,
               overlap_km2, pct_pc_area
        FROM property.v_postal_flood_risk
        WHERE {where}
        ORDER BY pct_pc_area DESC NULLS LAST
        LIMIT :limit
        """
    )
    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# Per-listing detail (newly enriched fields)
# ---------------------------------------------------------------------------

@router.get("/listing/{source_listing_id}/detail")
async def listing_detail(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    source_listing_id: str,
) -> dict[str, Any]:
    """Full detail-enriched listing snapshot (fees, condition, heating, energy)."""
    sql = text(
        """
        SELECT l.source, l.source_listing_id, l.status,
               l.first_seen_at, l.last_seen_at, l.asking_price,
               l.living_area_m2, l.year_built, l.rooms, l.energy_class,
               l.maintenance_fee_eur, l.financial_fee_eur, l.water_fee_eur,
               l.parking_fee_eur, l.sauna_fee_eur,
               l.share_of_liabilities_eur, l.debt_free_price,
               l.apartment_condition_code, l.heating_method, l.heating_method_code,
               l.building_material, l.has_lift, l.has_sauna, l.lot_ownership_code,
               l.detail_fetched_at,
               pa.canonical_address, pa.postal_code, pa.municipality, pa.lat, pa.lon
        FROM property.listing l
        JOIN property.property_asset pa ON pa.asset_id = l.asset_id
        WHERE l.source_listing_id = :sid
        LIMIT 1
        """
    )
    result = await session.execute(sql, {"sid": source_listing_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Listing not found")
    return dict(row._mapping)


@router.get("/listing/{source_listing_id}/history")
async def listing_history(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    source_listing_id: str,
) -> list[dict[str, Any]]:
    """Price snapshots for a listing, ordered ASC for sparkline rendering (Story 5)."""
    sql = text(
        """
        SELECT lps.snapshot_date,
               lps.asking_price::float AS asking_price,
               lps.price_per_m2::float AS price_per_m2
        FROM property.listing_price_snapshot lps
        JOIN property.listing l ON l.listing_id = lps.listing_id
        WHERE l.source_listing_id = :sid
        ORDER BY lps.snapshot_date ASC
        """
    )
    result = await session.execute(sql, {"sid": source_listing_id})
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/listing/{source_listing_id}/transactions")
async def listing_transactions(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    source_listing_id: str,
    limit: int = Query(20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Recent transactions in the same neighborhood as a listing (Story 7).

    Joins:
      listing -> asset -> postal_code_area.name (neighborhood)
      then transactions WHERE (municipality, neighborhood) matches.

    Only 3 of 30k+ transactions carry asset_id today, so the match is by
    municipality + neighborhood text rather than asset_id. This sees every
    listed transaction in the neighborhood, not just the same building.
    """
    sql = text(
        """
        WITH listing_loc AS (
            SELECT pa.municipality,
                   pca.name AS area_name
            FROM property.listing l
            JOIN property.property_asset pa ON pa.asset_id = l.asset_id
            LEFT JOIN property.postal_code_area pca
                ON pca.postal_code = pa.postal_code
            WHERE l.source_listing_id = :sid
            LIMIT 1
        )
        SELECT t.transaction_date,
               t.sale_date,
               t.sale_date_precision,
               t.transaction_price::float AS transaction_price,
               t.transaction_type,
               t.building_type,
               t.living_area_m2,
               t.price_per_m2::float AS price_per_m2,
               t.room_config,
               t.condition,
               t.year_built,
               t.municipality,
               t.neighborhood
        FROM property.transaction t, listing_loc ll
        WHERE t.municipality = ll.municipality
          AND t.neighborhood = ll.area_name
        ORDER BY t.transaction_date DESC
        LIMIT :n
        """
    )
    result = await session.execute(sql, {"sid": source_listing_id, "n": limit})
    return [dict(row._mapping) for row in result.fetchall()]
