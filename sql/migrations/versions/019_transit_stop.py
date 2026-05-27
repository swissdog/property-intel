"""Add transit_stop table for GTFS public-transport accessibility scoring.

Stores GTFS stops (currently HSL capital-region static feed) so we can compute
a per-asset transit-access score (building_features.transit_score_proxy) via
PostGIS spatial queries. See scripts/fetch_transit_score.py.

A GiST index on geom::geography lets ST_DWithin(...::geography, ..., metres)
use the index for the per-asset nearest-stop / stop-count aggregation.

Revision ID: 019_transit_stop
Revises: 018_sale_date_precision
Create Date: 2026-05-27
"""

from alembic import op

revision = "019_transit_stop"
down_revision = "018_sale_date_precision"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.transit_stop (
            feed        VARCHAR(40)  NOT NULL,
            stop_id     VARCHAR(120) NOT NULL,
            name        VARCHAR(200),
            lat         DOUBLE PRECISION NOT NULL,
            lon         DOUBLE PRECISION NOT NULL,
            geom        GEOMETRY(POINT, 4326) NOT NULL,
            fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (feed, stop_id)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_transit_stop_geog "
        f"ON {SCHEMA}.transit_stop USING GIST ((geom::geography))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.transit_stop")
