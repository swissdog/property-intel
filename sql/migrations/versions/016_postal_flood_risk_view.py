"""Add v_postal_flood_risk view — spatial join postal_code_area ↔ flood_risk_area.

Returns one row per (postal_code, scenario) pair with any flood-zone
overlap, plus the absolute overlap area (km²) and its share of the
postal-code polygon. Drives the /api/v1/intel/flood-risk endpoint.

DDL kept in `sql/migrations/sql/016_postal_flood_risk.sql` for readability;
this migration splits the file by `;` and executes each statement
separately because asyncpg refuses to prepare multi-command strings.

Revision ID: 016_postal_flood_risk_view
Revises: 015_flood_risk
Create Date: 2026-05-10
"""

from pathlib import Path

from alembic import op


revision = "016_postal_flood_risk_view"
down_revision = "015_flood_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "016_postal_flood_risk.sql"
    statements = [s.strip() for s in sql_path.read_text().split(";") if s.strip()]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS property.v_postal_flood_risk CASCADE")
