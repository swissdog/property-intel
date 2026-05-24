"""Integration tests for property.lookup_postal_code() PostGIS function.

Requires the property-db Docker container running with the postal_code_area
table seeded (run scripts/seed_postal_areas.py first).

Run:
    pytest tests/integration/test_postal_code_lookup.py -v
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.getenv(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    "postgresql+asyncpg://property:property_dev@localhost:5433/property_intel",
)


@pytest.fixture(scope="module")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
async def engine():
    eng = create_async_engine(DB_URL, future=True)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_helsinki_centre_resolves(engine):
    """Mannerheimintie 10 → 00100 (Helsinki keskusta)."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT property.lookup_postal_code(60.17, 24.94)")
        )
        assert result.scalar_one() == "00100"


@pytest.mark.asyncio
async def test_munkkiniemi_resolves(engine):
    """Huopalahdentie 14 → 00330 (Munkkiniemi)."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT property.lookup_postal_code(60.1998, 24.8829)")
        )
        assert result.scalar_one() == "00330"


@pytest.mark.asyncio
async def test_outside_finland_returns_null(engine):
    """Tukholma → NULL (no Finnish postal polygon contains it)."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT property.lookup_postal_code(59.33, 18.07)")
        )
        assert result.scalar_one() is None


@pytest.mark.asyncio
async def test_water_edge_case_uses_nearest_fallback(engine):
    """Hakaniemenranta 18 (60.1786, 24.9626) sits ~50 m off polygon edge.
    The 500-m nearest-fallback should still return a valid postal code."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT property.lookup_postal_code(60.1786, 24.9626)")
        )
        pc = result.scalar_one()
        assert pc is not None
        # Hakaniemi is 00530 area
        assert pc.startswith("00")


@pytest.mark.asyncio
async def test_polygons_seeded(engine):
    """Sanity check: postal_code_area has the expected polygon count."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM property.postal_code_area")
        )
        count = result.scalar_one()
        # ~3026 polygons currently from Paavo 2024 layer
        assert count > 2900, f"Too few polygons: {count}"
