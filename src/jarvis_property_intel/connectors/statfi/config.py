"""Tilastokeskus (Statistics Finland) PxWeb connector configuration.

Pre-configures table paths for the most commonly used housing-price
datasets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StatFiConfig:
    """Configuration for the StatFi PxWeb connector.

    Attributes:
        base_url: PxWeb API root URL.
        timeout: HTTP request timeout in seconds.
        max_concurrent: Maximum concurrent requests to the PxWeb API.
        apartment_prices_table: Table path for old apartment prices and
            transaction volumes by postal code area.
        price_index_table: Table path for old apartment price index
            (quarterly).
        monthly_index_table: Table path for monthly price indices.
    """

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "STATFI_PXWEB_BASE_URL",
            "https://pxdata.stat.fi/PXWeb/api/v1/fi",
        ),
    )
    timeout: float = 30.0
    max_concurrent: int = 5

    # Pre-configured table paths -----------------------------------------
    # Tables were renamed in 2024: 112p→13mt, 112q→13mp, 112r→13ms
    # Path structure changed from StatFin/asu/ashi/<sub>/ to StatFin/ashi/
    # 2026-06-08: PxWeb dropped the long "statfin_ashi_pxt_" id form in the
    # active StatFin database (long form → 400); only the short "<id>.px"
    # works. NOTE: StatFin_Passiivi archive tables still REQUIRE the long
    # "statfinpas_*" form — this change applies to active StatFin only.
    apartment_prices_table: str = (
        "StatFin/ashi/13mt.px"
    )
    price_index_table: str = (
        "StatFin/ashi/13mp.px"
    )
    monthly_index_table: str = (
        "StatFin/ashi/13ms.px"
    )
