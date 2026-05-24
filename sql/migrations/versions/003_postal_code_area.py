"""Add postal_code_area + property.lookup_postal_code() reverse-geocoder.

postal_code_area: Tilastokeskus Paavo postal-code polygons, used to
fill listing postal_code from lat/lon when source APIs (Oikotie /api/cards)
do not include it. Seed via scripts/seed_postal_areas.py.

lookup_postal_code(lat, lon): SQL helper using ST_Intersects with a
500 m nearest-polygon fallback for harbor edges / GPS jitter.

Revision ID: 003_postal_code_area
Revises: 002_history
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa


revision = "003_postal_code_area"
down_revision = "002_history"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.postal_code_area (
            postal_code        VARCHAR(5)  PRIMARY KEY,
            name               VARCHAR(200),
            municipality_code  VARCHAR(10),
            municipality_name  VARCHAR(100),
            geom               GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
            fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_layer       VARCHAR(80)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_postal_code_area_geom "
        f"ON {SCHEMA}.postal_code_area USING GIST (geom)"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_postal_code_area_municipality "
        f"ON {SCHEMA}.postal_code_area (municipality_name)"
    )

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.lookup_postal_code(
            p_lat DOUBLE PRECISION, p_lon DOUBLE PRECISION
        )
        RETURNS VARCHAR(5)
        LANGUAGE sql
        STABLE
        AS $$
            WITH point AS (
                SELECT ST_SetSRID(ST_MakePoint(p_lon, p_lat), 4326) AS g
            ),
            inside AS (
                SELECT pca.postal_code
                FROM {SCHEMA}.postal_code_area pca, point
                WHERE ST_Intersects(pca.geom, point.g)
                ORDER BY ST_Area(pca.geom) ASC
                LIMIT 1
            ),
            nearest AS (
                SELECT pca.postal_code
                FROM {SCHEMA}.postal_code_area pca, point
                WHERE NOT EXISTS (SELECT 1 FROM inside)
                  AND ST_DWithin(pca.geom::geography, point.g::geography, 500.0)
                ORDER BY pca.geom <-> point.g
                LIMIT 1
            )
            SELECT postal_code FROM inside
            UNION ALL
            SELECT postal_code FROM nearest
            LIMIT 1;
        $$
        """
    )


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.lookup_postal_code(DOUBLE PRECISION, DOUBLE PRECISION)")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.postal_code_area")
