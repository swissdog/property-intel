"""Add v_mortgage_market view — composite Finnish mortgage cost lens.

Joins property.bof_housing_loan_metric (rates, volume, stock) with the
12-month Euribor monthly average from property.interest_rate to expose
the implied bank margin (new_loan_rate − euribor_12m).

DDL kept in `sql/migrations/sql/014_mortgage_market.sql` for readability;
this migration splits the file by `;` and executes each statement
separately because asyncpg refuses to prepare multi-command strings.

Revision ID: 014_mortgage_market_view
Revises: 013_bof_housing_loans
Create Date: 2026-05-10
"""

from pathlib import Path

from alembic import op


revision = "014_mortgage_market_view"
down_revision = "013_bof_housing_loans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "014_mortgage_market.sql"
    statements = [s.strip() for s in sql_path.read_text().split(";") if s.strip()]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS property.v_mortgage_market CASCADE")
