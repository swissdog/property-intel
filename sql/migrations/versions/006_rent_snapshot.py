"""Add rent_snapshot table for StatFi rent data — required for yield calc.

Source: Tilastokeskus statfinpas_asvu_pxt_13eb (postal-code level rents,
2015Q1-2025Q4) + statfin_asvu_pxt_15fa (current quarter).

Yield per postal code = median_rent_per_m2 × 12 / median_sold_per_m2,
joined to area_snapshot at (postal_code, period_start).

Revision ID: 006_rent_snapshot
Revises: 005_interest_rates
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "006_rent_snapshot"
down_revision = "005_interest_rates"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "rent_snapshot",
        sa.Column("rent_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("postal_code", sa.String(5), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("room_count_band", sa.String(8), nullable=False),
        sa.Column("median_rent_per_m2", sa.Numeric(8, 2)),
        sa.Column("rental_contract_count", sa.Integer),
        sa.Column("source", sa.String(40), nullable=False, server_default="statfi_asvu"),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("postal_code", "period_start", "period_end", "room_count_band",
                            name="uq_rent_snapshot",
                            postgresql_nulls_not_distinct=True),
        schema=SCHEMA,
    )
    op.create_index("ix_rent_snapshot_pc", "rent_snapshot", ["postal_code"], schema=SCHEMA)
    op.create_index("ix_rent_snapshot_period", "rent_snapshot",
                    ["period_start", "period_end"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("rent_snapshot", schema=SCHEMA)
