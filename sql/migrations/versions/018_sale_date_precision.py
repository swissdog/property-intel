"""Add honest sale-date semantics: sale_date + sale_date_precision.

Problem (havaittu 2026-05-26 dedup-siivouksessa): KVKL/hintatiedot.fi-lähde ei
palauta kauppapäivää lainkaan, joten kirjoittaja asetti `transaction_date`-arvoksi
*ingest-päivän* → ingest-päivä esitettiin kauppapäivänä. Aineiston todellinen
tarkkuus on neljännesvuositasoa (KVKL), eikä per-rivin oikeaa päivää ole.

Korjaus (docs/transaction-dates-and-eu-sources.md, Vaihe 1 kohta 2): erota oikea
kauppapäivä omaksi kentäkseen ja merkitse tarkkuus eksplisiittisesti:
- `sale_date`            = todellinen kauppapäivä, NULL jos ei tiedossa.
- `sale_date_precision`  = 'exact' | 'quarter' | 'unknown'.

`transaction_date` (NOT NULL, legacy) säilyy taaksepäin yhteensopivuuden vuoksi
ingest-proxynä; kanoninen totuuskenttä on jatkossa `sale_date` + precision.

Backfill:
- MML-lähteet (kiinteistöjen kauppahintarekisteri) tuovat oikean kauppapäivän
  (`kauppapvm`/`luovutuspvm`) → sale_date = transaction_date, precision = 'exact'.
- Kaikki muut (nyk. hintatiedot_kvkl) → sale_date = NULL, precision = 'unknown'.

Revision ID: 018_sale_date_precision
Revises: 017_listing_seller_class
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa

revision = "018_sale_date_precision"
down_revision = "017_listing_seller_class"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.add_column(
        "transaction",
        sa.Column("sale_date", sa.Date, nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "transaction",
        sa.Column(
            "sale_date_precision",
            sa.String(10),
            nullable=False,
            server_default="unknown",
        ),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_transaction_sale_date_precision",
        "transaction",
        "sale_date_precision IN ('exact', 'quarter', 'unknown')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_transaction_sale_date",
        "transaction",
        ["sale_date"],
        schema=SCHEMA,
    )

    # Backfill: MML-lähteiden transaction_date ON oikea kauppapäivä → exact.
    # Muut lähteet (hintatiedot_kvkl) pitävät defaultin (NULL / 'unknown').
    op.execute(
        f"""
        UPDATE {SCHEMA}.transaction
           SET sale_date = transaction_date,
               sale_date_precision = 'exact'
         WHERE source ILIKE '%mml%';
        """
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_sale_date", table_name="transaction", schema=SCHEMA)
    op.drop_constraint(
        "ck_transaction_sale_date_precision",
        "transaction",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("transaction", "sale_date_precision", schema=SCHEMA)
    op.drop_column("transaction", "sale_date", schema=SCHEMA)
