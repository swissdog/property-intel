"""Hintatiedot.fi connector configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HintatiedotConfig:
    """Configuration for the hintatiedot.fi transaction scraper.

    Attributes:
        base_url: Root URL for asuntojen.hintatiedot.fi.
        max_pages: Maximum pages to fetch per city (safety limit).
        timeout: HTTP request timeout in seconds.
        delay_between_requests: Seconds to wait between page fetches (politeness).
    """

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "HINTATIEDOT_BASE_URL",
            "https://asuntojen.hintatiedot.fi",
        ),
    )
    max_pages: int = 50
    timeout: float = 30.0
    delay_between_requests: float = 1.0
