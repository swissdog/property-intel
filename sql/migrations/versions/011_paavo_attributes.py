"""Add paavo_attribute long-format table for postal-code demographics.

Paavo serves ~100 columns per postal-code area (population, income,
education, housing stock, employment). Storing them in a long format
keeps the schema stable as Paavo evolves between annual releases —
new attributes appear as new rows, not new columns.

Source: Tilastokeskus Paavo WFS (postialue:pno_tilasto_<year>).
Geometry already lives in property.postal_code_area (migration 003);
this table holds the per-attribute time series.

Revision ID: 011_paavo_attributes
Revises: 010_municipality_region
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa


revision = "011_paavo_attributes"
down_revision = "010_municipality_region"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.create_table(
        "paavo_attribute",
        sa.Column("postal_code", sa.String(5), nullable=False),
        sa.Column("year", sa.SmallInteger, nullable=False),
        sa.Column("attribute_code", sa.String(40), nullable=False),
        sa.Column("attribute_label", sa.Text),
        sa.Column("value", sa.Numeric(16, 2)),
        sa.Column("source_layer", sa.String(80)),
        sa.Column("fetched_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("postal_code", "year", "attribute_code",
                                name="pk_paavo_attribute"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_paavo_attribute_pc_year",
        "paavo_attribute",
        ["postal_code", "year"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_paavo_attribute_code",
        "paavo_attribute",
        ["attribute_code"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("paavo_attribute", schema=SCHEMA)
