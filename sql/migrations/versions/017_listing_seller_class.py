"""Add v_listing_seller_class view — asumisoikeus | rakennusliike | lkv | unknown.

Derives a per-listing seller_class from agency_name brand text so downstream
analyses can split supply by operator type. Asumisoikeus operators (VASO,
Asuntosäätiö, TA-Asumisoikeus, Haso, Avain, Mangrove, Jaso) are pulled out
of the rakennusliike bucket they were colliding with under simpler patterns.

DDL kept in `sql/migrations/sql/017_listing_seller_class.sql` for readability;
this migration splits the file by `;` and executes each statement separately
because asyncpg refuses to prepare multi-command strings.

Revision ID: 017_listing_seller_class
Revises: 016_postal_flood_risk_view
Create Date: 2026-05-15
"""

from pathlib import Path

from alembic import op


revision = "017_listing_seller_class"
down_revision = "016_postal_flood_risk_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parent.parent / "sql" / "017_listing_seller_class.sql"
    statements = [s.strip() for s in sql_path.read_text().split(";") if s.strip()]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS property.v_listing_seller_class CASCADE")
