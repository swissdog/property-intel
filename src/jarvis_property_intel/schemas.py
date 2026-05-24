"""Pydantic v2 response models for the Property Intelligence API."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    version: str = Field(..., examples=["0.1.0"])
    database: str = Field(..., examples=["connected"])


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


class LatestListingSummary(BaseModel):
    """Compact listing info attached to a property summary."""

    listing_id: uuid.UUID
    source: str
    source_listing_id: str
    status: str
    asking_price: float | None = None
    living_area_m2: float | None = None
    first_seen_at: datetime
    last_seen_at: datetime


class PropertySummary(BaseModel):
    """Lightweight property representation for search results."""

    asset_id: uuid.UUID
    asset_type: str
    canonical_address: str
    postal_code: str
    municipality: str
    lat: float | None = None
    lon: float | None = None
    source_confidence: float
    latest_listing: LatestListingSummary | None = None


class PaginatedPropertyResponse(BaseModel):
    """Paginated wrapper for property search results."""

    items: list[PropertySummary]
    total: int
    limit: int
    offset: int


class ListingDetail(BaseModel):
    """Full listing record."""

    listing_id: uuid.UUID
    source: str
    source_listing_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    asking_price: float | None = None
    living_area_m2: float | None = None
    year_built: int | None = None
    rooms: int | None = None
    lot_area_m2: float | None = None
    energy_class: str | None = None


class TransactionDetail(BaseModel):
    """Matched transaction record."""

    transaction_id: uuid.UUID
    transaction_date: date
    transaction_price: float
    transaction_type: str
    source: str


class BuildingFeaturesDetail(BaseModel):
    """Building-level enrichment data."""

    heating_type: str | None = None
    sauna: bool | None = None
    garage: bool | None = None
    waterfront_proxy: float | None = None
    school_distance_m: float | None = None
    elevation: float | None = None
    transit_score_proxy: float | None = None


class PropertyDetail(BaseModel):
    """Full property asset with listing history and transactions."""

    asset_id: uuid.UUID
    asset_type: str
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
    building_features: BuildingFeaturesDetail | None = None
    listings: list[ListingDetail]
    transactions: list[TransactionDetail]


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class ListingEventItem(BaseModel):
    """Single listing event in a property timeline."""

    event_id: uuid.UUID
    listing_id: uuid.UUID
    event_type: str
    event_at: datetime
    old_value: str | None = None
    new_value: str | None = None


class TimelineResponse(BaseModel):
    """Ordered list of listing events for a property."""

    asset_id: uuid.UUID
    events: list[ListingEventItem]


# ---------------------------------------------------------------------------
# Comparables
# ---------------------------------------------------------------------------


class ComparableProperty(BaseModel):
    """A comparable property with similarity score."""

    asset_id: uuid.UUID
    canonical_address: str
    postal_code: str
    municipality: str
    asset_type: str
    asking_price: float | None = None
    living_area_m2: float | None = None
    year_built: int | None = None
    distance_km: float | None = None
    similarity_score: float = Field(
        ..., ge=0.0, le=1.0, description="0-1 similarity score"
    )


class ComparablesResponse(BaseModel):
    """List of comparable properties for a given asset."""

    asset_id: uuid.UUID
    comparables: list[ComparableProperty]


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


class ValuationResponse(BaseModel):
    """Estimated valuation range for a property."""

    asset_id: uuid.UUID
    low: float = Field(..., description="Low-end valuation estimate (EUR)")
    fair: float = Field(..., description="Fair-value estimate (EUR)")
    high: float = Field(..., description="High-end valuation estimate (EUR)")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Model confidence 0-1"
    )
    model_version: str
    computed_at: datetime


# ---------------------------------------------------------------------------
# Area
# ---------------------------------------------------------------------------


class AreaMarketSummary(BaseModel):
    """Market KPIs for a postal-code area."""

    postal_code: str
    municipality: str | None = None
    segment: str | None = None
    period: str = Field(..., examples=["2025-Q4"])
    median_ask_m2: float | None = None
    median_sold_m2: float | None = None
    dom_median: float | None = Field(
        None, description="Median days-on-market"
    )
    inventory_count: int | None = None
    price_cut_ratio: float | None = Field(
        None,
        description="Share of listings with at least one price reduction",
    )
    income_median: float | None = None
    owner_occupancy_ratio: float | None = None


class AreaSnapshotItem(BaseModel):
    """Single time-series data point for an area."""

    snapshot_id: uuid.UUID
    postal_code: str
    municipality: str
    period_start: date
    period_end: date
    segment: str | None = None
    median_ask_m2: float | None = None
    median_sold_m2: float | None = None
    dom_median: float | None = None
    inventory_count: int | None = None
    price_cut_ratio: float | None = None
    income_median: float | None = None
    owner_occupancy_ratio: float | None = None


class AreaHistoryResponse(BaseModel):
    """Time series of area snapshots."""

    postal_code: str
    snapshots: list[AreaSnapshotItem]
