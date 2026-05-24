"""Add per-listing detail fields needed for yield/cashflow analysis.

Sourced from Oikotie /api/card/{id} (priceData + adData). These fields
matter for valuation and yield calculation:
- maintenance_fee_eur, financial_fee_eur — monthly housing-company charges
- water_fee_eur, parking_fee_eur — recurring per-unit costs
- share_of_liabilities_eur, debt_free_price — buyer's all-in price
- apartment_condition_code — 1..5 numeric condition scale
- heating_method (text) + heating_method_code — primary heating
- building_material — concrete/wood/brick (durability proxy)
- has_lift, has_sauna — amenity flags
- lot_ownership_code — own (1) vs leased (2)

Backfilled by scripts/backfill_listing_details.py.

Revision ID: 004_listing_detail_fields
Revises: 003_postal_code_area
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa


revision = "004_listing_detail_fields"
down_revision = "003_postal_code_area"
branch_labels = None
depends_on = None

SCHEMA = "property"

NEW_COLUMNS = [
    ("maintenance_fee_eur",       sa.Numeric(10, 2)),
    ("financial_fee_eur",         sa.Numeric(10, 2)),
    ("water_fee_eur",             sa.Numeric(10, 2)),
    ("parking_fee_eur",           sa.Numeric(10, 2)),
    ("sauna_fee_eur",             sa.Numeric(10, 2)),
    ("share_of_liabilities_eur",  sa.Numeric(12, 2)),
    ("debt_free_price",           sa.Numeric(12, 2)),
    ("apartment_condition_code",  sa.SmallInteger()),
    ("heating_method",            sa.String(80)),
    ("heating_method_code",       sa.String(10)),
    ("building_material",         sa.String(40)),
    ("has_lift",                  sa.Boolean()),
    ("has_sauna",                 sa.Boolean()),
    ("lot_ownership_code",        sa.SmallInteger()),
    ("detail_fetched_at",         sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, kind in NEW_COLUMNS:
        op.add_column("listing", sa.Column(name, kind, nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_listing_detail_fetched_at",
        "listing",
        ["detail_fetched_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_listing_detail_fetched_at", "listing", schema=SCHEMA)
    for name, _ in NEW_COLUMNS:
        op.drop_column("listing", name, schema=SCHEMA)
