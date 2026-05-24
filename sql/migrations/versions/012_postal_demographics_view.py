"""Add v_postal_demographics view pivoting paavo_attribute into wide format.

Pivots ~15 key attribute_codes (population, income, education, employment,
housing) for the latest-year Paavo release per postal code. Composes with
v_postal_investor_lens (yield/growth) and the kunta/maakunta lookup.

DDL kept in `sql/migrations/sql/012_postal_demographics.sql` for readability;
this migration splits the file by `;` and executes each statement
separately because asyncpg refuses to prepare multi-command strings.

Revision ID: 012_postal_demographics_view
Revises: 011_paavo_attributes
Create Date: 2026-05-10
"""

from pathlib import Path

from alembic import op


revision = "012_postal_demographics_view"
down_revision = "011_paavo_attributes"
branch_labels = None
depends_on = None


def _execute_sql_file(filename: str) -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / filename
    text = sql_path.read_text()
    # Split on `;` at end of line — asyncpg cannot prepare multi-statement strings.
    # Naive split is safe here because our view DDL never contains string
    # literals or function bodies that include semicolons.
    statements = [s.strip() for s in text.split(";") if s.strip()]
    for stmt in statements:
        op.execute(stmt)


def upgrade() -> None:
    # Drop first because CREATE OR REPLACE VIEW cannot change the column list
    # (e.g. if column names or order are revised later).
    op.execute("DROP VIEW IF EXISTS property.v_postal_demographics CASCADE")
    _execute_sql_file("012_postal_demographics.sql")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS property.v_postal_demographics CASCADE")
