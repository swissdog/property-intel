"""SQLAlchemy 2.x ORM models for property-intel."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "property"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


# ---------------------------------------------------------------------------
# raw_snapshot
# ---------------------------------------------------------------------------


class RawSnapshot(Base):
    __tablename__ = "raw_snapshot"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    parse_version: Mapped[str] = mapped_column(String(50), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)

    __table_args__ = (
        Index("ix_raw_snapshot_source_record", "source", "source_record_id"),
        Index("ix_raw_snapshot_source_fetched", "source", "fetched_at"),
    )


# ---------------------------------------------------------------------------
# property_asset
# ---------------------------------------------------------------------------


class PropertyAsset(Base):
    __tablename__ = "property_asset"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    canonical_address: Mapped[str] = mapped_column(String(500), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(10), nullable=False)
    municipality: Mapped[str] = mapped_column(String(100), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    parcel_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    building_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    housing_company_name: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    source_confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    # relationships
    listings: Mapped[list[Listing]] = relationship(back_populates="asset")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="asset")
    building_features: Mapped[BuildingFeatures | None] = relationship(
        back_populates="asset", uselist=False
    )

    __table_args__ = (
        Index("ix_property_asset_postal_code", "postal_code"),
        Index("ix_property_asset_municipality", "municipality"),
        Index("ix_property_asset_lat_lon", "lat", "lon"),
        Index(
            "uq_property_asset_parcel_id",
            "parcel_id",
            unique=True,
            postgresql_where=text("parcel_id IS NOT NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# listing
# ---------------------------------------------------------------------------


class Listing(Base):
    __tablename__ = "listing"

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_listing_id: Mapped[str] = mapped_column(String(255), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active"
    )
    asking_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    living_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    year_built: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    energy_class: Mapped[str | None] = mapped_column(String(10), nullable=True)
    json_blob: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # relationships
    asset: Mapped[PropertyAsset | None] = relationship(back_populates="listings")
    events: Mapped[list[ListingEvent]] = relationship(back_populates="listing")

    __table_args__ = (
        UniqueConstraint("source", "source_listing_id", name="uq_listing_source"),
        Index("ix_listing_asset_id", "asset_id"),
        Index("ix_listing_status", "status"),
    )


# ---------------------------------------------------------------------------
# listing_event
# ---------------------------------------------------------------------------


class ListingEvent(Base):
    __tablename__ = "listing_event"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.listing.listing_id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # relationships
    listing: Mapped[Listing] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_listing_event_listing_at", "listing_id", "event_at"),
    )


# ---------------------------------------------------------------------------
# transaction
# ---------------------------------------------------------------------------


class Transaction(Base):
    __tablename__ = "transaction"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        nullable=True,
    )
    parcel_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Legacy column (NOT NULL): for KVKL this holds the ingest date, not a real
    # sale date — kept for backward compat. Canonical truth is sale_date below.
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Real sale date (NULL when unknown) + explicit precision flag so consumers
    # never mistake the ingest date for the sale date.
    sale_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sale_date_precision: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="unknown"
    )
    transaction_price: Mapped[float] = mapped_column(Float, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # relationships
    asset: Mapped[PropertyAsset | None] = relationship(back_populates="transactions")

    __table_args__ = (
        UniqueConstraint(
            "source", "source_record_id", name="uq_transaction_source"
        ),
        Index("ix_transaction_asset_id", "asset_id"),
        Index("ix_transaction_date", "transaction_date"),
        Index("ix_transaction_sale_date", "sale_date"),
        Index("ix_transaction_parcel_id", "parcel_id"),
        CheckConstraint(
            "sale_date_precision IN ('exact', 'quarter', 'unknown')",
            name="ck_transaction_sale_date_precision",
        ),
    )


# ---------------------------------------------------------------------------
# building_features
# ---------------------------------------------------------------------------


class BuildingFeatures(Base):
    __tablename__ = "building_features"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        primary_key=True,
    )
    heating_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sauna: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    garage: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    waterfront_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    school_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation: Mapped[float | None] = mapped_column(Float, nullable=True)
    transit_score_proxy: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # relationships
    asset: Mapped[PropertyAsset] = relationship(
        back_populates="building_features"
    )


# ---------------------------------------------------------------------------
# area_snapshot
# ---------------------------------------------------------------------------


class AreaSnapshot(Base):
    __tablename__ = "area_snapshot"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    postal_code: Mapped[str] = mapped_column(String(10), nullable=False)
    municipality: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    segment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    median_ask_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_sold_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    dom_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    inventory_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_cut_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    income_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    owner_occupancy_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "postal_code",
            "period_start",
            "period_end",
            "segment",
            name="uq_area_snapshot_period",
        ),
        Index("ix_area_snapshot_postal_period", "postal_code", "period_end"),
    )


# ---------------------------------------------------------------------------
# entity_match
# ---------------------------------------------------------------------------


class EntityMatch(Base):
    __tablename__ = "entity_match"

    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_id_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        nullable=False,
    )
    asset_id_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        nullable=False,
    )
    match_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, nullable=False)
    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    resolved_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.property_asset.asset_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_entity_match_pair", "asset_id_a", "asset_id_b"),
    )
