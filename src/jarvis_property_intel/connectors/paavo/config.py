"""Paavo (Tilastokeskus postal-code area statistics) connector configuration.

Paavo data is served via a WFS (OGC Web Feature Service) endpoint
hosted by Statistics Finland's GeoServer instance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PaavoConfig:
    """Configuration for the Paavo WFS connector.

    Attributes:
        wfs_url: Base URL of the GeoServer WFS endpoint.
        layer: WFS layer (typename) for postal code statistics.  The
            layer name includes the release year suffix
            (e.g. ``postialue:pno_tilasto_2024``).
        timeout: HTTP request timeout in seconds.  WFS responses can be
            large, so a generous default is used.
        max_concurrent: Maximum concurrent WFS requests.
        max_features: Maximum number of features per WFS request
            (``maxFeatures`` / ``count``).
        srs_name: Coordinate reference system for the returned geometries.
    """

    wfs_url: str = field(
        default_factory=lambda: os.getenv(
            "PAAVO_WFS_URL",
            "https://geo.stat.fi/geoserver/postialue/wfs",
        ),
    )
    layer: str = field(
        default_factory=lambda: os.getenv(
            "PAAVO_WFS_LAYER",
            "postialue:pno_tilasto_2024",
        ),
    )
    timeout: float = 60.0
    max_concurrent: int = 3
    max_features: int = 5000
    srs_name: str = "EPSG:4326"
