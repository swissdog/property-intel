"""Add interest_rate table for ECB / Euribor mortgage-relevant rate history.

Mortgage interest rate is the dominant macro driver of Finnish housing demand.
This table stores both the underlying ECB policy rates and the actual mortgage
indices (Euribor 1M/3M/6M/12M).

Source: ECB Statistical Data Warehouse (SDW) — data-api.ecb.europa.eu

Revision ID: 005_interest_rates
Revises: 004_listing_detail_fields
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa


revision = "005_interest_rates"
down_revision = "004_listing_detail_fields"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "interest_rate",
        sa.Column("rate_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rate_type", sa.String(40), nullable=False),
        sa.Column("observation_date", sa.Date, nullable=False),
        sa.Column("frequency", sa.String(1), nullable=False),  # D, M, Q
        sa.Column("value_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("source_series", sa.String(80), nullable=False),
        sa.Column("source_provider", sa.String(40), nullable=False, server_default="ECB"),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("rate_type", "observation_date",
                            name="uq_interest_rate_type_date"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_interest_rate_date",
        "interest_rate",
        ["observation_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_interest_rate_type_date",
        "interest_rate",
        ["rate_type", "observation_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("interest_rate", schema=SCHEMA)
