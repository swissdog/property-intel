"""Oikotie.fi connector configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OikotieConfig:
    """Configuration for the Oikotie consumer API connector.

    Tokens are acquired dynamically from the homepage on each session.
    """

    base_url: str = "https://asunnot.oikotie.fi"
    timeout: float = 30.0
    max_concurrent: int = 2  # Be polite
    request_delay: float = 1.0  # Seconds between requests
    page_size: int = 24
    max_pages: int = field(
        default_factory=lambda: int(os.getenv("OIKOTIE_MAX_PAGES", "10")),
    )
    # Default card type: apartments for sale
    card_type: int = 100
    # Helsinki city location ID
    default_locations: str = '[[64,6,"Helsinki"]]'
