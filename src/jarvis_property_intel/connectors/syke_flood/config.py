"""SYKE (Finnish Environment Institute) flood-risk REST config.

SYKE publishes flood-hazard data via the ArcGIS REST API (the legacy
INSPIRE WFS endpoint is no longer reachable as of 2026-05). Each scenario
is one (MapServer, layerId) tuple under the /arcgis/rest/services/Tulva/
folder; this config groups them so the connector can iterate over all
scenarios in one fetch_dataset() call.

Default service URL is paikkatieto.ymparisto.fi — no API key required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SykeFloodLayer:
    """One scenario layer published by SYKE's ArcGIS REST API.

    Attributes:
        scenario: Stable internal scenario tag stored on each polygon row
            (``"100y"``, ``"250y"``, ``"significant"``).
        service: MapServer service path under
            /arcgis/rest/services/Tulva/, e.g.
            ``"Tulvariskiaineistot_perusskenaariot_toistuvuuksittain"``.
        layer_id: Numeric layer id within the MapServer.
        description: Human-readable label used for logging / UI.
    """

    scenario: str
    service: str
    layer_id: int
    description: str


# Default layer set — covers the headline scenarios published in 2024.
# Layer ids verified 2026-05-10 against
# https://paikkatieto.ymparisto.fi/arcgis/rest/services/Tulva/.
DEFAULT_LAYERS: tuple[SykeFloodLayer, ...] = (
    SykeFloodLayer(
        scenario="100y",
        service="Tulvariskiaineistot_perusskenaariot_toistuvuuksittain",
        layer_id=22,
        description="Maaritetyt tulva-alueet, vesistotulva, 1/100a (1%), vesisyvyys",
    ),
    SykeFloodLayer(
        scenario="250y",
        service="Tulvariskiaineistot_perusskenaariot_toistuvuuksittain",
        layer_id=16,
        description="Maaritetyt tulva-alueet, vesistotulva, 1/250a (0.4%), vesisyvyys",
    ),
    SykeFloodLayer(
        scenario="significant",
        service="Tulvariskialueet_2024_ehdotetut",
        layer_id=1,
        description="Nykyiset merkittavat ja muut tulvariskialueet",
    ),
)


@dataclass(frozen=True, slots=True)
class SykeFloodConfig:
    """Configuration for the SYKE flood-risk REST connector.

    Attributes:
        rest_base_url: Base URL of the SYKE ArcGIS REST services root,
            e.g. ``https://paikkatieto.ymparisto.fi/arcgis/rest/services/Tulva``.
        layers: Tuple of :class:`SykeFloodLayer` to fetch.
        timeout: HTTP request timeout in seconds.
        max_concurrent: Maximum concurrent requests.
        max_features: Page size for /query (record count per request).
        out_sr: Coordinate reference system for the returned geometries
            (4326 for WGS-84 lat/lon — what PostGIS stores natively here).
    """

    rest_base_url: str = field(
        default_factory=lambda: os.getenv(
            "SYKE_FLOOD_REST_URL",
            "https://paikkatieto.ymparisto.fi/arcgis/rest/services/Tulva",
        ),
    )
    layers: tuple[SykeFloodLayer, ...] = DEFAULT_LAYERS
    timeout: float = 180.0
    max_concurrent: int = 2
    max_features: int = 500
    out_sr: int = 4326
