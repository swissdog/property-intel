"""Add bof_housing_loan_metric for Finnish mortgage-market monthly indicators.

Source: ECB Statistical Data Warehouse (Bank of Finland reporters).
- MIR (Monetary Financial Institutions Interest Rates): rates + margins
- BSI (Bank Balance Sheet Items): outstanding-amount stocks + new business

Long-format: one row per (period, metric_code) — same shape as
property.paavo_attribute, keeps the schema stable when ECB adds new
breakdowns.

Revision ID: 013_bof_housing_loans
Revises: 012_postal_demographics_view
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "013_bof_housing_loans"
down_revision = "012_postal_demographics_view"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "bof_housing_loan_metric",
        sa.Column("period", sa.Date, nullable=False),
        sa.Column("metric_code", sa.String(40), nullable=False),
        sa.Column("value", sa.Numeric(16, 4), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("source_series", sa.String(120), nullable=False),
        sa.Column("source_provider", sa.String(40), nullable=False, server_default="ECB_BoF"),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("period", "metric_code", name="pk_bof_housing_loan_metric"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bof_housing_loan_period",
        "bof_housing_loan_metric",
        ["period"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_bof_housing_loan_metric_period",
        "bof_housing_loan_metric",
        ["metric_code", "period"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("bof_housing_loan_metric", schema=SCHEMA)
