"""Domain / DTO models for property-intel (Pydantic v2)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import (
    AssetType,
    DataSource,
    ListingEventType,
    ListingStatus,
    MatchStatus,
    TransactionType,
)


class PropertyAsset(BaseModel):
    model_config = {"frozen": False}

    asset_id: UUID = Field(default_factory=uuid4)
    asset_type: AssetType
    canonical_address: str
    postal_code: str
    municipality: str
    lat: float | None = None
    lon: float | None = None
    parcel_id: str | None = None
    building_id: str | None = None
    housing_company_name: str | None = None
    source_confidence: float
    created_at: datetime
    updated_at: datetime


class Listing(BaseModel):
    model_config = {"frozen": False}

    listing_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID | None = None
    source: DataSource
    source_listing_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    status: ListingStatus
    asking_price: float | None = None
    living_area_m2: float | None = None
    year_built: int | None = None
    rooms: int | None = None
    lot_area_m2: float | None = None
    description_text: str | None = None
    energy_class: str | None = None
    json_blob: dict | None = None


class ListingEvent(BaseModel):
    model_config = {"frozen": False}

    event_id: UUID = Field(default_factory=uuid4)
    listing_id: UUID
    event_type: ListingEventType
    event_at: datetime
    old_value: str | None = None
    new_value: str | None = None


class Transaction(BaseModel):
    model_config = {"frozen": False}

    transaction_id: UUID = Field(default_factory=uuid4)
    asset_id: UUID | None = None
    parcel_id: str | None = None
    transaction_date: date
    transaction_price: float
    transaction_type: TransactionType
    source: DataSource
    source_record_id: str


class BuildingFeatures(BaseModel):
    model_config = {"frozen": False}

    asset_id: UUID
    heating_type: str | None = None
    sauna: bool | None = None
    garage: bool | None = None
    waterfront_proxy: float | None = None
    school_distance_m: float | None = None
    elevation: float | None = None
    transit_score_proxy: float | None = None


class AreaSnapshot(BaseModel):
    model_config = {"frozen": False}

    snapshot_id: UUID = Field(default_factory=uuid4)
    postal_code: str
    municipality: str
    period_start: date
    period_end: date
    segment: AssetType | None = None
    median_ask_m2: float | None = None
    median_sold_m2: float | None = None
    dom_median: float | None = None
    inventory_count: int | None = None
    price_cut_ratio: float | None = None
    income_median: float | None = None
    owner_occupancy_ratio: float | None = None


class EntityMatch(BaseModel):
    model_config = {"frozen": False}

    match_id: UUID = Field(default_factory=uuid4)
    asset_id_a: UUID
    asset_id_b: UUID
    match_score: float
    match_reason: str
    match_status: MatchStatus
    resolved_asset_id: UUID | None = None


class RawSnapshot(BaseModel):
    model_config = {"frozen": False}

    snapshot_id: UUID = Field(default_factory=uuid4)
    source: DataSource
    url: str | None = None
    source_record_id: str | None = None
    fetched_at: datetime
    parse_version: str
    storage_path: str
