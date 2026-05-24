"""Main entity-resolution engine for properties.

Standalone library -- no database, API, or framework dependencies.
Consumers provide candidates in, get match results out, and handle
persistence themselves.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from .models import MatchResult, PropertyCandidate, ResolutionEvent
from .strategies import (
    CoordinateProximityMatch,
    ExactAddressMatch,
    HousingCompanyMatch,
    ImageHashMatch,
    MatchStrategy,
    ParcelIdMatch,
)


class EntityResolver:
    """Resolves property identities across sources.

    Standalone library -- no database or API dependencies.
    Consumers provide candidates and handle persistence of results.

    Example::

        resolver = EntityResolver()
        results = resolver.resolve_batch(new_candidates, existing_records)
        for r in results:
            if not r.review_needed:
                print(f"Auto-confirmed: {r.reason}")
    """

    def __init__(
        self,
        strategies: list[MatchStrategy] | None = None,
        auto_confirm_threshold: float = 0.90,
        review_threshold: float = 0.70,
    ) -> None:
        self._strategies: list[MatchStrategy] = strategies or [
            ParcelIdMatch(),
            ExactAddressMatch(),
            HousingCompanyMatch(),
            CoordinateProximityMatch(),
            ImageHashMatch(),
        ]
        self._auto_confirm_threshold = auto_confirm_threshold
        self._review_threshold = review_threshold
        self._event_handlers: list[Callable[[ResolutionEvent], None]] = []

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def on_event(self, handler: Callable[[ResolutionEvent], None]) -> None:
        """Register an event handler for resolution events."""
        self._event_handlers.append(handler)

    def _emit_event(self, event: ResolutionEvent) -> None:
        """Dispatch an event to all registered handlers."""
        for handler in self._event_handlers:
            handler(event)

    # ------------------------------------------------------------------
    # Core comparison
    # ------------------------------------------------------------------

    def compare(
        self, a: PropertyCandidate, b: PropertyCandidate
    ) -> MatchResult:
        """Compare two candidates using all registered strategies.

        Runs every strategy and keeps the best score.  If two or more
        strategies independently score above 0.7, the best score is
        boosted by 0.05 to reflect cross-strategy corroboration.

        ``review_needed`` is set to ``True`` when the score falls between
        the review threshold and the auto-confirm threshold.
        """
        best_score: float = 0.0
        best_strategy: str = "none"
        best_reason: str = "No strategy matched"
        agreeing_count: int = 0

        for strategy in self._strategies:
            s = strategy.score(a, b)
            if s is None:
                continue
            if s > 0.7:
                agreeing_count += 1
            if s > best_score:
                best_score = s
                best_strategy = strategy.name
                best_reason = strategy.explain(a, b)

        # Cross-strategy boost
        if agreeing_count >= 2:
            best_score = min(best_score + 0.05, 1.0)
            best_reason += f" [+0.05 boost: {agreeing_count} strategies agree]"

        review_needed = (
            self._review_threshold <= best_score < self._auto_confirm_threshold
        )

        return MatchResult(
            candidate_a=a,
            candidate_b=b,
            score=best_score,
            reason=best_reason,
            strategy=best_strategy,
            review_needed=review_needed,
        )

    # ------------------------------------------------------------------
    # Batch resolution
    # ------------------------------------------------------------------

    def resolve_batch(
        self,
        candidates: list[PropertyCandidate],
        existing: list[PropertyCandidate] | None = None,
    ) -> list[MatchResult]:
        """Resolve a batch of new candidates against existing records.

        Every candidate in *candidates* is compared to every record in
        *existing* (or to every other candidate if *existing* is ``None``).

        Returns all matches scoring at or above ``review_threshold``,
        sorted by score descending.  Matches at or above
        ``auto_confirm_threshold`` are auto-confirmed and emit a
        ``match_confirmed`` event.
        """
        comparisons = existing if existing is not None else candidates
        results: list[MatchResult] = []

        for candidate in candidates:
            for other in comparisons:
                # Skip self-comparison
                if (
                    candidate.source_id == other.source_id
                    and candidate.source_record_id == other.source_record_id
                ):
                    continue

                result = self.compare(candidate, other)

                if result.score < self._review_threshold:
                    continue

                results.append(result)

                # Auto-confirm high-confidence matches
                if result.score >= self._auto_confirm_threshold:
                    self._emit_event(
                        ResolutionEvent(
                            event_type="match_confirmed",
                            match_id=uuid.uuid4().hex,
                            asset_id_a=(
                                f"{candidate.source_id}:"
                                f"{candidate.source_record_id}"
                            ),
                            asset_id_b=(
                                f"{other.source_id}:"
                                f"{other.source_record_id}"
                            ),
                            resolved_asset_id=None,
                            timestamp=datetime.now(timezone.utc),
                            metadata={
                                "score": result.score,
                                "strategy": result.strategy,
                                "reason": result.reason,
                            },
                        )
                    )

        # Sort best matches first
        results.sort(key=lambda r: r.score, reverse=True)
        return results
