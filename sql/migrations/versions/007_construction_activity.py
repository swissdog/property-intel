"""Add construction_activity table for StatFi rakennusluvat / aloitukset / valmistuneet.

Source: StatFin/raku/statfin_raku_pxt_156f.px (1995M01 onwards).
Granularity: maakunta × month × phase (permit/start/completion) × building class.

Backfilled by scripts/fetch_statfi_construction.py.

Revision ID: 007_construction_activity
Revises: 006_rent_snapshot
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "007_construction_activity"
down_revision = "006_rent_snapshot"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "construction_activity",
        sa.Column("activity_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("region_code", sa.String(10), nullable=False),
        sa.Column("period_year_month", sa.CHAR(7), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("phase_code", sa.SmallInteger, nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("building_class_code", sa.String(10), nullable=False),
        sa.Column("new_dwellings", sa.Integer),
        sa.Column("floor_area_m2", sa.Numeric(12, 1)),
        sa.Column("volume_m3", sa.Numeric(14, 1)),
        sa.Column("activity_count", sa.Integer),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("region_code", "period_year_month", "phase_code", "building_class_code",
                            name="uq_construction_activity"),
        schema=SCHEMA,
    )
    op.create_index("ix_construction_region", "construction_activity",
                    ["region_code", "period_start"], schema=SCHEMA)
    op.create_index("ix_construction_period", "construction_activity",
                    ["period_start"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("construction_activity", schema=SCHEMA)
