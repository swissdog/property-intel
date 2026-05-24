"""MML (Maanmittauslaitos) connector configuration.

Controls API endpoint selection (OGC API Features vs legacy REST),
authentication, and client behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MMLConfig:
    """Configuration for the MML transaction connector.

    All settings can be overridden via environment variables so the connector
    works out-of-the-box in different deployment environments.

    Attributes:
        base_url: Root URL for the MML open-data platform.
        api_key: API key issued by MML (required for authenticated endpoints).
        api_version: Which backend to use — ``"ogc"`` for the new OGC API
            Features endpoint (spring 2026+) or ``"legacy"`` for the older
            REST interface.
        timeout: HTTP request timeout in seconds.
        max_concurrent: Maximum number of concurrent in-flight requests
            (rate-limiter width).
        page_size: Default number of items per page when paginating.
    """

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "MML_API_BASE_URL",
            "https://avoindata.maanmittauslaitos.fi",
        ),
    )
    api_key: str = field(
        default_factory=lambda: os.getenv("MML_API_KEY", ""),
    )
    api_version: str = field(
        default_factory=lambda: os.getenv("MML_API_VERSION", "ogc"),
    )
    timeout: float = 30.0
    max_concurrent: int = 10
    page_size: int = 1000

    def __post_init__(self) -> None:
        if self.api_version not in ("ogc", "legacy"):
            raise ValueError(
                f"MML_API_VERSION must be 'ogc' or 'legacy', got {self.api_version!r}"
            )
