"""Add municipality_region lookup for kunta→maakunta join.

Required for v_supply_demand drill-down per region. Migration data uses
KU091 codes (kunta), construction data uses MK01 codes (maakunta) — this
table bridges them. Source: api.stat.fi classification service correspondence
table for `kunta_1_<year>0101#maakunta_1_<year>0101`.

Seeded by scripts/seed_municipality_region.py.

Revision ID: 010_municipality_region
Revises: 009_analytical_views
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "010_municipality_region"
# Reorder (fresh-init-korjaus): siirretty 009:n eteen (009:n näkymät tarvitsevat
# tämän taulun). Ketju: 008 → 010 → 009 → 011.
down_revision = "008_migration_activity"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "municipality_region",
        sa.Column("municipality_code", sa.String(8), primary_key=True),
        sa.Column("municipality_name", sa.String(60)),
        sa.Column("region_code", sa.String(8), nullable=False),
        sa.Column("region_name", sa.String(60)),
        sa.Column("classification_year", sa.Integer, nullable=False, server_default="2025"),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index("ix_municipality_region_region", "municipality_region",
                    ["region_code"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("municipality_region", schema=SCHEMA)
