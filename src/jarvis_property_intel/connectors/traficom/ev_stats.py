"""Traficom vehicle stock — EV adoption by municipality.

Fetches passenger car counts from the Traficom PxWeb API ("Vehicles in
traffic" table) grouped by municipality and driving power, then computes
EV penetration rates (BEV + PHEV as a share of total vehicles).

The PxWeb API endpoint:
    POST https://trafi2.stat.fi/PXWeb/api/v1/en/TraFi/
         Liikennekaytossa_olevat_ajoneuvot/010_kanta_tau_101.px

Driving power codes of interest:
    YH  = Total (all power types)
    04  = Electricity (BEV)
    39  = Petrol/Electricity plug-in hybrid (PHEV)
    44  = Diesel/Electricity plug-in hybrid (PHEV)

Usage::

    from jarvis_property_intel.connectors.traficom.ev_stats import TraficomEVStats

    ev = TraficomEVStats()
    data = await ev.fetch_ev_stats()
    # data == {"Helsinki": {"total_vehicles": 248247, "bev_count": 15843, ...}, ...}

    await ev.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PxWeb constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://trafi2.stat.fi/PXWeb/api/v1/en/TraFi"
_TABLE_PATH = "Liikennekaytossa_olevat_ajoneuvot/010_kanta_tau_101.px"

# Driving-power dimension codes
_POWER_TOTAL = "YH"
_POWER_BEV = "04"       # Electricity (battery electric)
_POWER_PHEV_P = "39"    # Petrol/Electricity plug-in hybrid
_POWER_PHEV_D = "44"    # Diesel/Electricity plug-in hybrid

# Make dimension: total only
_MAKE_TOTAL = "YH"

# Year of first registration: total (all years combined)
_YEAR_TOTAL = "YH"


@dataclass(frozen=True, slots=True)
class MunicipalityEVData:
    """EV adoption data for a single municipality."""

    municipality_code: str
    municipality_name: str
    total_vehicles: int
    bev_count: int
    phev_count: int
    ev_total: int
    ev_pct: float


@dataclass
class TraficomEVStats:
    """Fetch and compute EV penetration rates per Finnish municipality.

    Talks to the Traficom PxWeb API for the "Passenger cars in traffic"
    table (``010_kanta_tau_101.px``).
    """

    base_url: str = _BASE_URL
    table_path: str = _TABLE_PATH
    timeout: float = 60.0
    max_concurrent: int = 3
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _rate_limiter: asyncio.Semaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rate_limiter = asyncio.Semaphore(self.max_concurrent)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_municipality_codes(self) -> dict[str, str]:
        """Return a mapping of municipality codes to names from the table metadata.

        Returns:
            Dict like ``{"KU091": "Helsinki", "KU049": "Espoo", ...}``.
        """
        client = await self._get_client()
        url = f"{self.base_url}/{self.table_path}"
        async with self._rate_limiter:
            resp = await client.get(url)
            resp.raise_for_status()
        meta = resp.json()

        area_var = next(
            (v for v in meta["variables"] if v["code"] == "Alue"), None
        )
        if area_var is None:
            raise ValueError("Could not find 'Alue' variable in table metadata")

        return {
            code: name
            for code, name in zip(area_var["values"], area_var["valueTexts"])
            if code.startswith("KU")  # Skip aggregate rows like MA1, 200, X
        }

    async def fetch_ev_stats(
        self,
        municipality_codes: list[str] | None = None,
    ) -> dict[str, MunicipalityEVData]:
        """Fetch EV adoption data for municipalities.

        Args:
            municipality_codes: Optional list of municipality codes
                (e.g. ``["KU091", "KU049"]``).  If ``None``, fetches for
                **all** municipalities (may be slow on first call).

        Returns:
            Dict mapping municipality name to its :class:`MunicipalityEVData`.
        """
        # Resolve municipality names
        code_to_name = await self.fetch_municipality_codes()

        if municipality_codes is None:
            municipality_codes = list(code_to_name.keys())

        # PxWeb has a practical limit on response size.  For all ~310
        # municipalities we can fetch in one call (the table is relatively
        # small when Make=Total and Year=Total).
        raw = await self._query_pxweb(municipality_codes)
        return self._parse_response(raw, code_to_name)

    async def fetch_ev_stats_dict(
        self,
        municipality_codes: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Same as :meth:`fetch_ev_stats` but returns plain dicts.

        Convenient for JSON serialization and quick inspection.
        """
        data = await self.fetch_ev_stats(municipality_codes)
        return {
            name: {
                "municipality_code": d.municipality_code,
                "total_vehicles": d.total_vehicles,
                "bev_count": d.bev_count,
                "phev_count": d.phev_count,
                "ev_total": d.ev_total,
                "ev_pct": round(d.ev_pct, 2),
            }
            for name, d in data.items()
        }

    # ------------------------------------------------------------------
    # PxWeb query construction & parsing
    # ------------------------------------------------------------------

    async def _query_pxweb(
        self, municipality_codes: list[str]
    ) -> dict[str, Any]:
        """POST a PxWeb query and return the parsed JSON response."""
        url = f"{self.base_url}/{self.table_path}"
        body = {
            "query": [
                {
                    "code": "Alue",
                    "selection": {
                        "filter": "item",
                        "values": municipality_codes,
                    },
                },
                {
                    "code": "Merkki",
                    "selection": {
                        "filter": "item",
                        "values": [_MAKE_TOTAL],
                    },
                },
                {
                    "code": "Käyttöönottovuosi",
                    "selection": {
                        "filter": "item",
                        "values": [_YEAR_TOTAL],
                    },
                },
                {
                    "code": "Käyttövoima",
                    "selection": {
                        "filter": "item",
                        "values": [
                            _POWER_TOTAL,
                            _POWER_BEV,
                            _POWER_PHEV_P,
                            _POWER_PHEV_D,
                        ],
                    },
                },
            ],
            "response": {"format": "json"},
        }

        client = await self._get_client()
        async with self._rate_limiter:
            logger.debug("Traficom PxWeb POST %s (%d municipalities)", url, len(municipality_codes))
            resp = await client.post(url, json=body)
            resp.raise_for_status()
        return resp.json()

    def _parse_response(
        self,
        raw: dict[str, Any],
        code_to_name: dict[str, str],
    ) -> dict[str, MunicipalityEVData]:
        """Parse PxWeb flat-table JSON into structured EV data.

        The response format is::

            {
                "columns": [...],
                "data": [
                    {"key": ["KU091", "YH", "YH", "YH"], "values": ["248247"]},
                    {"key": ["KU091", "YH", "YH", "04"], "values": ["15843"]},
                    ...
                ]
            }
        """
        rows: list[dict[str, Any]] = raw.get("data", [])

        # Accumulate per-municipality
        accum: dict[str, dict[str, int]] = {}
        for row in rows:
            keys = row.get("key", [])
            values = row.get("values", [])
            if len(keys) < 4 or not values:
                continue

            muni_code = keys[0]   # e.g. "KU091"
            power_code = keys[3]  # e.g. "YH", "04", "39", "44"

            try:
                count = int(values[0])
            except (ValueError, IndexError):
                count = 0

            if muni_code not in accum:
                accum[muni_code] = {
                    "total": 0,
                    "bev": 0,
                    "phev_p": 0,
                    "phev_d": 0,
                }

            if power_code == _POWER_TOTAL:
                accum[muni_code]["total"] = count
            elif power_code == _POWER_BEV:
                accum[muni_code]["bev"] = count
            elif power_code == _POWER_PHEV_P:
                accum[muni_code]["phev_p"] = count
            elif power_code == _POWER_PHEV_D:
                accum[muni_code]["phev_d"] = count

        # Build final result
        result: dict[str, MunicipalityEVData] = {}
        for muni_code, counts in accum.items():
            name = code_to_name.get(muni_code, muni_code)
            total = counts["total"]
            bev = counts["bev"]
            phev = counts["phev_p"] + counts["phev_d"]
            ev_total = bev + phev
            ev_pct = (ev_total / total * 100) if total > 0 else 0.0

            result[name] = MunicipalityEVData(
                municipality_code=muni_code,
                municipality_name=name,
                total_vehicles=total,
                bev_count=bev,
                phev_count=phev,
                ev_total=ev_total,
                ev_pct=ev_pct,
            )

        return result
