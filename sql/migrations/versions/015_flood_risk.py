"""Add flood_risk_area for SYKE INSPIRE flood-hazard polygons.

Source: SYKE (Finnish Environment Institute) INSPIRE Natural Risk Zones
WFS service. Three scenarios stored together:
    100y          — 100-year recurrence interval flood hazard
    250y          — 250-year recurrence interval flood hazard
    significant   — Nationally designated significant flood-risk area

Geometry: MULTIPOLYGON / EPSG:4326 (server-side reproject from EPSG:3067).

Revision ID: 015_flood_risk
Revises: 014_mortgage_market_view
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "015_flood_risk"
down_revision = "014_mortgage_market_view"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    # postgis extension is already created by migration 003; safe to assert.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.flood_risk_area (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scenario        VARCHAR(20) NOT NULL,
            source_layer    VARCHAR(120) NOT NULL,
            source_feature_id VARCHAR(120),
            properties      JSONB,
            geom            GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_flood_risk_area_feature
                UNIQUE (scenario, source_feature_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_flood_risk_area_geom "
        f"ON {SCHEMA}.flood_risk_area USING GIST (geom)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_flood_risk_area_scenario "
        f"ON {SCHEMA}.flood_risk_area (scenario)"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.flood_risk_area CASCADE")
