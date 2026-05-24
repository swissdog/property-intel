"""Add migration_activity table for StatFi muuttoliike (population flows).

Source: StatFin/muutl/statfin_muutl_pxt_11ae.px (1990 onwards).
Granularity: municipality × year. Net migration is the primary
demand signal for housing markets.

Backfilled by scripts/fetch_statfi_migration.py.

Revision ID: 008_migration_activity
Revises: 007_construction_activity
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "008_migration_activity"
down_revision = "007_construction_activity"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "migration_activity",
        sa.Column("activity_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("municipality_code", sa.String(8), nullable=False),
        sa.Column("period_year", sa.Integer, nullable=False),
        sa.Column("population_year_end", sa.Integer),
        sa.Column("natural_increase", sa.Integer),
        sa.Column("inter_muni_in", sa.Integer),
        sa.Column("inter_muni_out", sa.Integer),
        sa.Column("inter_muni_net", sa.Integer),
        sa.Column("intl_immigration", sa.Integer),
        sa.Column("intl_emigration", sa.Integer),
        sa.Column("intl_net", sa.Integer),
        sa.Column("total_net_migration", sa.Integer),
        sa.Column("pop_change", sa.Integer),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("municipality_code", "period_year",
                            name="uq_migration_activity"),
        schema=SCHEMA,
    )
    op.create_index("ix_migration_year", "migration_activity",
                    ["period_year"], schema=SCHEMA)
    op.create_index("ix_migration_muni_year", "migration_activity",
                    ["municipality_code", "period_year"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("migration_activity", schema=SCHEMA)
