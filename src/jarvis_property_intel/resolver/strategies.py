"""Match strategies for property entity resolution.

Each strategy implements a specific heuristic for determining whether two
PropertyCandidate records refer to the same real-world property.  Strategies
are pure functions over candidate pairs -- no IO, no database, no network.
"""

from __future__ import annotations

import math
import re
from typing import Protocol

from .models import PropertyCandidate

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MatchStrategy(Protocol):
    """Protocol for match strategies."""

    name: str

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        """Return match score 0-1, or None if strategy doesn't apply."""
        ...

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        """Human-readable explanation of the match."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STREET_ABBREVS: list[tuple[re.Pattern[str], str]] = [
    # "katu" / "k." -> "katu" (canonical)
    (re.compile(r"\bk\.\s*"), "katu "),
    # "tie" / "t." -> "tie"
    (re.compile(r"\bt\.\s*"), "tie "),
    # "vägen" / "v." -> "vägen"
    (re.compile(r"\bv\.\s*"), "vägen "),
    # "gatan" / "g." -> "gatan"
    (re.compile(r"\bg\.\s*"), "gatan "),
]

_APARTMENT_PATTERN = re.compile(
    r"(?:as\.?\s*|bst\.?\s*|huoneisto\s*|apt\.?\s*)?([A-Za-z]?)\s*(\d+)",
)


def normalize_finnish_address(address: str) -> str:
    """Normalize Finnish address for comparison.

    * Lowercase and strip surrounding whitespace.
    * Expand common street-type abbreviations (k. -> katu, t. -> tie).
    * Normalize apartment designators:  "A 1" -> "a1", "as. 1" -> "1".
    * Collapse multiple spaces.
    """
    text = address.lower().strip()

    # Expand abbreviations
    for pattern, replacement in _STREET_ABBREVS:
        text = pattern.sub(replacement, text)

    # Normalize apartment numbers: "A 1" -> "a1", "as. 3" -> "3"
    def _norm_apt(m: re.Match[str]) -> str:
        letter = m.group(1).lower()
        number = m.group(2)
        return f"{letter}{number}" if letter else number

    text = _APARTMENT_PATTERN.sub(_norm_apt, text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def haversine_distance_m(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Distance in meters between two WGS-84 points using the haversine formula."""
    R = 6_371_000  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def _area_similar(a: float | None, b: float | None, tolerance: float) -> bool:
    """Return True if two areas are within *tolerance* fraction of each other."""
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    avg = (a + b) / 2
    if avg == 0:
        return False
    return abs(a - b) / avg <= tolerance


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Levenshtein similarity ratio in [0, 1].  1 = identical."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    # Standard DP Levenshtein distance
    matrix: list[list[int]] = [
        [0] * (len2 + 1) for _ in range(len1 + 1)
    ]
    for i in range(len1 + 1):
        matrix[i][0] = i
    for j in range(len2 + 1):
        matrix[0][j] = j

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )

    distance = matrix[len1][len2]
    max_len = max(len1, len2)
    return 1.0 - distance / max_len


# ---------------------------------------------------------------------------
# Housing company name normalization
# ---------------------------------------------------------------------------

_COMPANY_PREFIXES = re.compile(
    r"\b(?:asunto-osakeyhti[oö]|asunto\s*oy|as\.?\s*oy|bostads\s*ab)\b",
    re.IGNORECASE,
)


def _normalize_company_name(name: str) -> str:
    """Normalize Finnish housing company names for comparison."""
    text = name.lower().strip()
    # Replace all company-type prefixes with canonical "as oy"
    text = _COMPANY_PREFIXES.sub("as oy", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


class ExactAddressMatch:
    """Exact canonical address + living area + year built."""

    name: str = "exact_address"

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        if a.address is None or b.address is None:
            return None

        norm_a = normalize_finnish_address(a.address)
        norm_b = normalize_finnish_address(b.address)

        if norm_a != norm_b:
            return None

        area_ok = _area_similar(a.living_area_m2, b.living_area_m2, 0.05)
        year_ok = (
            a.year_built is not None
            and b.year_built is not None
            and a.year_built == b.year_built
        )

        if area_ok and year_ok:
            return 0.95
        if area_ok:
            return 0.85
        # Address matches but no supporting data -- moderate confidence
        return 0.75

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        parts: list[str] = []
        norm_a = normalize_finnish_address(a.address or "")
        norm_b = normalize_finnish_address(b.address or "")

        if norm_a == norm_b:
            parts.append(f"Canonical address match: '{norm_a}'")
        else:
            parts.append(f"Address mismatch: '{norm_a}' vs '{norm_b}'")

        if _area_similar(a.living_area_m2, b.living_area_m2, 0.05):
            parts.append(
                f"Living area similar: {a.living_area_m2} vs {b.living_area_m2} m2"
            )
        if a.year_built and b.year_built:
            if a.year_built == b.year_built:
                parts.append(f"Same year built: {a.year_built}")
            else:
                parts.append(
                    f"Year built differs: {a.year_built} vs {b.year_built}"
                )

        return "; ".join(parts)


class CoordinateProximityMatch:
    """Coordinate proximity within 50 m + area similarity."""

    name: str = "coordinate_proximity"
    _max_distance_m: float = 50.0

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        if a.lat is None or a.lon is None or b.lat is None or b.lon is None:
            return None

        dist = haversine_distance_m(a.lat, a.lon, b.lat, b.lon)
        if dist > self._max_distance_m:
            return None

        base = 0.80
        # Adjust by area similarity if available
        if _area_similar(a.living_area_m2, b.living_area_m2, 0.10):
            base += 0.10
        # Closer distance boosts score slightly
        proximity_bonus = 0.05 * (1.0 - dist / self._max_distance_m)
        return min(base + proximity_bonus, 1.0)

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        if a.lat is None or a.lon is None or b.lat is None or b.lon is None:
            return "Coordinates not available"
        dist = haversine_distance_m(a.lat, a.lon, b.lat, b.lon)
        parts = [f"Distance: {dist:.1f} m"]
        if a.living_area_m2 and b.living_area_m2:
            parts.append(
                f"Area: {a.living_area_m2} vs {b.living_area_m2} m2"
            )
        return "; ".join(parts)


class HousingCompanyMatch:
    """Fuzzy housing company name + apartment characteristics."""

    name: str = "housing_company"
    _name_threshold: float = 0.85

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        if a.housing_company_name is None or b.housing_company_name is None:
            return None

        norm_a = _normalize_company_name(a.housing_company_name)
        norm_b = _normalize_company_name(b.housing_company_name)
        ratio = _levenshtein_ratio(norm_a, norm_b)

        if ratio < self._name_threshold:
            return None

        # Company name matches -- check apartment details
        apt_match = (
            a.apartment_number is not None
            and b.apartment_number is not None
            and a.apartment_number.strip().lower()
            == b.apartment_number.strip().lower()
        )

        if apt_match:
            return 0.90
        # Company matches but no apartment data -- lower confidence
        return 0.75

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        if a.housing_company_name is None or b.housing_company_name is None:
            return "Housing company name not available"
        norm_a = _normalize_company_name(a.housing_company_name)
        norm_b = _normalize_company_name(b.housing_company_name)
        ratio = _levenshtein_ratio(norm_a, norm_b)
        parts = [
            f"Company name similarity: {ratio:.2f} "
            f"('{norm_a}' vs '{norm_b}')"
        ]
        if a.apartment_number and b.apartment_number:
            parts.append(
                f"Apartment: '{a.apartment_number}' vs '{b.apartment_number}'"
            )
        return "; ".join(parts)


class ParcelIdMatch:
    """Same parcel ID (kiinteistotunnus) -- near-certain for detached houses."""

    name: str = "parcel_id"

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        if a.parcel_id is None or b.parcel_id is None:
            return None

        if a.parcel_id.strip() == b.parcel_id.strip():
            return 0.98

        return None

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        if a.parcel_id is None or b.parcel_id is None:
            return "Parcel ID not available on both candidates"
        if a.parcel_id.strip() == b.parcel_id.strip():
            return f"Parcel ID match: {a.parcel_id}"
        return f"Parcel ID mismatch: {a.parcel_id} vs {b.parcel_id}"


class ImageHashMatch:
    """Shared perceptual image hashes."""

    name: str = "image_hash"

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        if not a.image_hashes or not b.image_hashes:
            return None

        shared = set(a.image_hashes) & set(b.image_hashes)
        if not shared:
            return None

        score = 0.75 + 0.05 * (len(shared) - 1)
        return min(score, 0.95)

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        if not a.image_hashes or not b.image_hashes:
            return "Image hashes not available"
        shared = set(a.image_hashes) & set(b.image_hashes)
        return (
            f"{len(shared)} shared image hash(es) out of "
            f"{len(set(a.image_hashes))} / {len(set(b.image_hashes))}"
        )


class CompositeMatch:
    """Runs all sub-strategies, takes highest score.

    If multiple strategies independently agree (score > 0.7), the best
    score is boosted by 0.05 to reflect the additional confidence from
    cross-strategy corroboration.
    """

    name: str = "composite"

    def __init__(
        self, strategies: list[MatchStrategy] | None = None
    ) -> None:
        self._strategies: list[MatchStrategy] = strategies or [
            ParcelIdMatch(),
            ExactAddressMatch(),
            HousingCompanyMatch(),
            CoordinateProximityMatch(),
            ImageHashMatch(),
        ]

    def score(self, a: PropertyCandidate, b: PropertyCandidate) -> float | None:
        scores: list[float] = []
        for strategy in self._strategies:
            s = strategy.score(a, b)
            if s is not None:
                scores.append(s)

        if not scores:
            return None

        best = max(scores)
        agreeing = [s for s in scores if s > 0.7]
        if len(agreeing) >= 2:
            best = min(best + 0.05, 1.0)

        return best

    def explain(self, a: PropertyCandidate, b: PropertyCandidate) -> str:
        parts: list[str] = []
        for strategy in self._strategies:
            s = strategy.score(a, b)
            if s is not None:
                parts.append(f"{strategy.name}: {s:.2f}")
        if not parts:
            return "No strategies matched"
        return "Composite — " + ", ".join(parts)
