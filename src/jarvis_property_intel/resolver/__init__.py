"""Property entity resolution library.

Standalone, pure-Python library for resolving property identities across
multiple data sources.  No database, API, or framework dependencies.

Usage::

    from jarvis_property_intel.resolver import EntityResolver, PropertyCandidate

    resolver = EntityResolver()
    result = resolver.compare(candidate_a, candidate_b)
    print(result.score, result.reason)
"""

from .engine import EntityResolver
from .models import MatchResult, PropertyCandidate, ResolutionEvent
from .strategies import (
    CompositeMatch,
    CoordinateProximityMatch,
    ExactAddressMatch,
    HousingCompanyMatch,
    ImageHashMatch,
    MatchStrategy,
    ParcelIdMatch,
    haversine_distance_m,
    normalize_finnish_address,
)

__all__ = [
    "EntityResolver",
    "PropertyCandidate",
    "MatchResult",
    "ResolutionEvent",
    "MatchStrategy",
    "ExactAddressMatch",
    "CoordinateProximityMatch",
    "HousingCompanyMatch",
    "ParcelIdMatch",
    "ImageHashMatch",
    "CompositeMatch",
    "haversine_distance_m",
    "normalize_finnish_address",
]
