"""Create property schema and all initial tables and materialized views.

Revision ID: 001_initial
Revises: None
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 0. Schema
    # ------------------------------------------------------------------
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ------------------------------------------------------------------
    # 1. raw_snapshot
    # ------------------------------------------------------------------
    op.create_table(
        "raw_snapshot",
        sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("source_record_id", sa.String(255), nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("parse_version", sa.String(50), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_raw_snapshot_source_record",
        "raw_snapshot",
        ["source", "source_record_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_raw_snapshot_source_fetched",
        "raw_snapshot",
        ["source", "fetched_at"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 2. property_asset
    # ------------------------------------------------------------------
    op.create_table(
        "property_asset",
        sa.Column("asset_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_type", sa.String(50), nullable=False),
        sa.Column("canonical_address", sa.String(500), nullable=False),
        sa.Column("postal_code", sa.String(10), nullable=False),
        sa.Column("municipality", sa.String(100), nullable=False),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lon", sa.Float, nullable=True),
        sa.Column("parcel_id", sa.String(100), nullable=True),
        sa.Column("building_id", sa.String(100), nullable=True),
        sa.Column("housing_company_name", sa.String(300), nullable=True),
        sa.Column("source_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_property_asset_postal_code",
        "property_asset",
        ["postal_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_property_asset_municipality",
        "property_asset",
        ["municipality"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_property_asset_lat_lon",
        "property_asset",
        ["lat", "lon"],
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_property_asset_parcel_id "
        f"ON {SCHEMA}.property_asset (parcel_id) "
        f"WHERE parcel_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # 3. listing
    # ------------------------------------------------------------------
    op.create_table(
        "listing",
        sa.Column("listing_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            nullable=True,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_listing_id", sa.String(255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("asking_price", sa.Float, nullable=True),
        sa.Column("living_area_m2", sa.Float, nullable=True),
        sa.Column("year_built", sa.Integer, nullable=True),
        sa.Column("rooms", sa.Integer, nullable=True),
        sa.Column("lot_area_m2", sa.Float, nullable=True),
        sa.Column("description_text", sa.Text, nullable=True),
        sa.Column("energy_class", sa.String(10), nullable=True),
        sa.Column("json_blob", sa.JSON, nullable=True),
        sa.UniqueConstraint("source", "source_listing_id", name="uq_listing_source"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_listing_asset_id",
        "listing",
        ["asset_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_listing_status",
        "listing",
        ["status"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 4. listing_event
    # ------------------------------------------------------------------
    op.create_table(
        "listing_event",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.listing.listing_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_listing_event_listing_at",
        "listing_event",
        ["listing_id", "event_at"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 5. transaction
    # ------------------------------------------------------------------
    op.create_table(
        "transaction",
        sa.Column("transaction_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            nullable=True,
        ),
        sa.Column("parcel_id", sa.String(100), nullable=True),
        sa.Column("transaction_date", sa.Date, nullable=False),
        sa.Column("transaction_price", sa.Float, nullable=False),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False),
        sa.UniqueConstraint(
            "source", "source_record_id", name="uq_transaction_source"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_transaction_asset_id",
        "transaction",
        ["asset_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_transaction_date",
        "transaction",
        ["transaction_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_transaction_parcel_id",
        "transaction",
        ["parcel_id"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 6. building_features
    # ------------------------------------------------------------------
    op.create_table(
        "building_features",
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            primary_key=True,
        ),
        sa.Column("heating_type", sa.String(50), nullable=True),
        sa.Column("sauna", sa.Boolean, nullable=True),
        sa.Column("garage", sa.Boolean, nullable=True),
        sa.Column("waterfront_proxy", sa.Float, nullable=True),
        sa.Column("school_distance_m", sa.Float, nullable=True),
        sa.Column("elevation", sa.Float, nullable=True),
        sa.Column("transit_score_proxy", sa.Float, nullable=True),
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 7. area_snapshot
    # ------------------------------------------------------------------
    op.create_table(
        "area_snapshot",
        sa.Column("snapshot_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("postal_code", sa.String(10), nullable=False),
        sa.Column("municipality", sa.String(100), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("segment", sa.String(50), nullable=True),
        sa.Column("median_ask_m2", sa.Float, nullable=True),
        sa.Column("median_sold_m2", sa.Float, nullable=True),
        sa.Column("dom_median", sa.Float, nullable=True),
        sa.Column("inventory_count", sa.Integer, nullable=True),
        sa.Column("price_cut_ratio", sa.Float, nullable=True),
        sa.Column("income_median", sa.Float, nullable=True),
        sa.Column("owner_occupancy_ratio", sa.Float, nullable=True),
        sa.UniqueConstraint(
            "postal_code",
            "period_start",
            "period_end",
            "segment",
            name="uq_area_snapshot_period",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_area_snapshot_postal_period",
        "area_snapshot",
        ["postal_code", "period_end"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 8. entity_match
    # ------------------------------------------------------------------
    op.create_table(
        "entity_match",
        sa.Column("match_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id_a",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            nullable=False,
        ),
        sa.Column(
            "asset_id_b",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            nullable=False,
        ),
        sa.Column("match_score", sa.Float, nullable=False),
        sa.Column("match_reason", sa.Text, nullable=False),
        sa.Column(
            "match_status", sa.String(20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "resolved_asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_entity_match_pair",
        "entity_match",
        ["asset_id_a", "asset_id_b"],
        schema=SCHEMA,
    )

    # ------------------------------------------------------------------
    # 9. Gold-layer materialized views
    # ------------------------------------------------------------------

    # 9a. latest_listing_state
    op.execute("""
        CREATE MATERIALIZED VIEW property.latest_listing_state AS
        SELECT
            l.listing_id,
            l.source,
            l.source_listing_id,
            l.first_seen_at,
            l.last_seen_at,
            l.status,
            l.asking_price,
            l.living_area_m2,
            l.year_built,
            l.rooms,
            l.lot_area_m2,
            l.energy_class,
            CASE
                WHEN l.living_area_m2 IS NOT NULL AND l.living_area_m2 > 0
                THEN ROUND((l.asking_price / l.living_area_m2)::numeric, 2)
                ELSE NULL
            END                                     AS asking_price_per_m2,
            EXTRACT(DAY FROM (CURRENT_TIMESTAMP - l.first_seen_at))::int
                                                    AS days_on_market,
            pa.asset_id,
            pa.asset_type,
            pa.canonical_address,
            pa.postal_code,
            pa.municipality,
            pa.lat,
            pa.lon,
            pa.parcel_id,
            pa.housing_company_name,
            bf.heating_type,
            bf.sauna,
            bf.garage,
            bf.waterfront_proxy,
            bf.school_distance_m,
            bf.elevation,
            bf.transit_score_proxy
        FROM property.listing l
        LEFT JOIN property.property_asset pa ON pa.asset_id = l.asset_id
        LEFT JOIN property.building_features bf ON bf.asset_id = pa.asset_id
        WHERE l.status = 'active'
    """)
    op.execute(
        "CREATE UNIQUE INDEX ON property.latest_listing_state (listing_id)"
    )
    op.execute(
        "CREATE INDEX ON property.latest_listing_state (postal_code)"
    )
    op.execute(
        "CREATE INDEX ON property.latest_listing_state (municipality)"
    )

    # 9b. price_change_history
    op.execute("""
        CREATE MATERIALIZED VIEW property.price_change_history AS
        SELECT
            le.event_id,
            le.listing_id,
            l.source,
            l.source_listing_id,
            pa.postal_code,
            pa.municipality,
            le.event_at,
            le.old_value::numeric                   AS old_price,
            le.new_value::numeric                   AS new_price,
            (le.new_value::numeric - le.old_value::numeric)
                                                    AS price_delta,
            CASE
                WHEN le.old_value::numeric IS NOT NULL AND le.old_value::numeric <> 0
                THEN ROUND(
                    ((le.new_value::numeric - le.old_value::numeric)
                     / le.old_value::numeric * 100)::numeric,
                    2
                )
                ELSE NULL
            END                                     AS price_change_pct
        FROM property.listing_event le
        JOIN property.listing l ON l.listing_id = le.listing_id
        LEFT JOIN property.property_asset pa ON pa.asset_id = l.asset_id
        WHERE le.event_type = 'price_changed'
    """)
    op.execute(
        "CREATE UNIQUE INDEX ON property.price_change_history (event_id)"
    )
    op.execute(
        "CREATE INDEX ON property.price_change_history (listing_id, event_at)"
    )
    op.execute(
        "CREATE INDEX ON property.price_change_history (postal_code, event_at)"
    )

    # 9c. market_velocity_by_postal_code
    op.execute("""
        CREATE MATERIALIZED VIEW property.market_velocity_by_postal_code AS
        WITH weeks AS (
            SELECT
                date_trunc('week', dd)::date        AS week_start,
                (date_trunc('week', dd) + INTERVAL '6 days')::date
                                                    AS week_end
            FROM generate_series(
                (SELECT date_trunc('week', MIN(first_seen_at)) FROM property.listing),
                CURRENT_DATE,
                '1 week'::interval
            ) dd
        ),
        listing_with_postal AS (
            SELECT
                l.listing_id,
                l.first_seen_at,
                l.last_seen_at,
                l.status,
                l.asking_price,
                pa.postal_code
            FROM property.listing l
            JOIN property.property_asset pa ON pa.asset_id = l.asset_id
            WHERE pa.postal_code IS NOT NULL
        ),
        weekly_active AS (
            SELECT
                w.week_start,
                w.week_end,
                lp.postal_code,
                lp.listing_id,
                lp.asking_price,
                EXTRACT(DAY FROM (
                    LEAST(lp.last_seen_at, w.week_end::timestamp WITH TIME ZONE)
                    - lp.first_seen_at
                ))::int                              AS dom
            FROM weeks w
            JOIN listing_with_postal lp
                ON lp.first_seen_at <= (w.week_end + 1)::timestamp WITH TIME ZONE
               AND lp.last_seen_at  >= w.week_start::timestamp WITH TIME ZONE
        ),
        new_per_week AS (
            SELECT
                date_trunc('week', first_seen_at)::date AS week_start,
                postal_code,
                COUNT(*)                                AS new_count
            FROM listing_with_postal
            GROUP BY 1, 2
        ),
        removed_per_week AS (
            SELECT
                date_trunc('week', last_seen_at)::date  AS week_start,
                postal_code,
                COUNT(*)                                AS removed_count
            FROM listing_with_postal
            WHERE status <> 'active'
            GROUP BY 1, 2
        )
        SELECT
            wa.week_start,
            wa.week_end,
            wa.postal_code,
            COUNT(DISTINCT wa.listing_id)                AS active_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wa.asking_price)
                                                         AS median_asking_price,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wa.dom)
                                                         AS median_dom,
            COALESCE(npw.new_count, 0)                   AS new_listings,
            COALESCE(rpw.removed_count, 0)               AS removed_listings
        FROM weekly_active wa
        LEFT JOIN new_per_week npw
            ON npw.week_start = wa.week_start AND npw.postal_code = wa.postal_code
        LEFT JOIN removed_per_week rpw
            ON rpw.week_start = wa.week_start AND rpw.postal_code = wa.postal_code
        GROUP BY wa.week_start, wa.week_end, wa.postal_code,
                 npw.new_count, rpw.removed_count
    """)
    op.execute(
        "CREATE UNIQUE INDEX ON property.market_velocity_by_postal_code "
        "(postal_code, week_start)"
    )
    op.execute(
        "CREATE INDEX ON property.market_velocity_by_postal_code (week_start)"
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # Drop materialized views (reverse order)
    # ------------------------------------------------------------------
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS property.market_velocity_by_postal_code"
    )
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS property.price_change_history"
    )
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS property.latest_listing_state"
    )

    # ------------------------------------------------------------------
    # Drop tables (reverse dependency order)
    # ------------------------------------------------------------------
    op.drop_table("entity_match", schema=SCHEMA)
    op.drop_table("area_snapshot", schema=SCHEMA)
    op.drop_table("building_features", schema=SCHEMA)
    op.drop_table("transaction", schema=SCHEMA)
    op.drop_table("listing_event", schema=SCHEMA)
    op.drop_table("listing", schema=SCHEMA)
    op.drop_table("property_asset", schema=SCHEMA)
    op.drop_table("raw_snapshot", schema=SCHEMA)

    # ------------------------------------------------------------------
    # Drop schema
    # ------------------------------------------------------------------
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
