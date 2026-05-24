"""Resolution data models for property entity matching.

Pure dataclasses with no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class PropertyCandidate:
    """A property record from any source, input to resolution."""

    source_id: str
    source_record_id: str
    address: str | None = None
    postal_code: str | None = None
    municipality: str | None = None
    lat: float | None = None
    lon: float | None = None
    living_area_m2: float | None = None
    year_built: int | None = None
    rooms: int | None = None
    lot_area_m2: float | None = None
    parcel_id: str | None = None
    building_id: str | None = None
    housing_company_name: str | None = None
    apartment_number: str | None = None
    image_hashes: list[str] | None = None


@dataclass
class MatchResult:
    """Result of comparing two candidates."""

    candidate_a: PropertyCandidate
    candidate_b: PropertyCandidate
    score: float  # 0.0 to 1.0
    reason: str  # human-readable explanation
    strategy: str  # which strategy produced this match
    review_needed: bool  # True if score is in uncertain range


@dataclass
class ResolutionEvent:
    """Event emitted when resolution state changes."""

    event_type: str  # match_confirmed, match_rejected, match_merged
    match_id: str
    asset_id_a: str
    asset_id_b: str
    resolved_asset_id: str | None
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
