"""Add analytical views (v_postal_investor_lens, v_yield_anomalies, etc).

These denormalized views are the analyst-ready output of the data product.
They compose listings, transactions, rents, construction, migration, and
rates into the shapes a paying customer would query.

DDL kept in `sql/migrations/sql/009_analytical_views.sql` for readability;
this migration just executes the file.

Revision ID: 009_analytical_views
Revises: 008_migration_activity
Create Date: 2026-05-10
"""

from pathlib import Path

from alembic import op


revision = "009_analytical_views"
down_revision = "008_migration_activity"
branch_labels = None
depends_on = None

VIEW_NAMES = (
    "v_postal_investor_lens",
    "v_yield_anomalies",
    "v_market_velocity_timeseries",
    "v_supply_demand",
    "v_national_headline",
)


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "009_analytical_views.sql"
    op.execute(sql_path.read_text())


def downgrade() -> None:
    for name in VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS property.{name} CASCADE")
