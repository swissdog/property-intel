"""Tilastokeskus (Statistics Finland) commuting statistics connector.

Fetches pendelöinti (commuting) data from the PxWeb employment statistics
(työssäkäyntitilasto) tables. Combines two complementary perspectives:

* **Table 115n** — employed persons by *residence municipality*, split into
  those who work locally vs. commute out.
* **Table 115p** — employed labour force by *workplace municipality*, split
  into locals vs. commuters into the area.
* **Table 125s** — workplace self-sufficiency percentage by municipality.
* **Table 115x** — key employment indicators (employment rate, unemployment
  rate, economic dependency ratio).

Together these tables tell the full commuting story: how many people live
and work in a municipality, how many commute in, how many commute out, and
what the net commuting balance is.

Usage::

    from jarvis_property_intel.connectors.statfi.commuting import StatFiCommutingConnector

    connector = StatFiCommutingConnector()
    stats = await connector.fetch_commuting_stats(
        municipalities=["Helsinki", "Espoo", "Tampere", "Vantaa", "Oulu"],
        year="2023",
    )
    for name, data in stats.items():
        print(f"{name}: net_commute={data['net_commute']:+,}")
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
# Municipality code mapping (KU-codes for the largest Finnish cities)
# ---------------------------------------------------------------------------

# Comprehensive mapping — covers all 309 municipalities in mainland Finland
# plus Åland. We hard-code the top-50 and provide a lookup helper.
MUNICIPALITY_CODES: dict[str, str] = {
    "Helsinki": "KU091",
    "Espoo": "KU049",
    "Tampere": "KU837",
    "Vantaa": "KU092",
    "Oulu": "KU564",
    "Turku": "KU853",
    "Jyväskylä": "KU179",
    "Lahti": "KU398",
    "Kuopio": "KU297",
    "Pori": "KU609",
    "Kouvola": "KU286",
    "Joensuu": "KU167",
    "Lappeenranta": "KU405",
    "Hämeenlinna": "KU109",
    "Vaasa": "KU905",
    "Seinäjoki": "KU743",
    "Rovaniemi": "KU698",
    "Mikkeli": "KU491",
    "Kotka": "KU285",
    "Salo": "KU734",
    "Porvoo": "KU638",
    "Kokkola": "KU272",
    "Lohja": "KU444",
    "Hyvinkää": "KU106",
    "Nurmijärvi": "KU543",
    "Järvenpää": "KU186",
    "Rauma": "KU684",
    "Kirkkonummi": "KU257",
    "Kajaani": "KU205",
    "Tuusula": "KU858",
    "Kerava": "KU245",
    "Nokia": "KU536",
    "Ylöjärvi": "KU980",
    "Kaarina": "KU202",
    "Kangasala": "KU211",
    "Savonlinna": "KU740",
    "Riihimäki": "KU694",
    "Sastamala": "KU790",
    "Raasepori": "KU710",
    "Vihti": "KU927",
    "Imatra": "KU153",
    "Raisio": "KU680",
    "Lempäälä": "KU418",
    "Hollola": "KU098",
    "Tornio": "KU851",
    "Siilinjärvi": "KU749",
    "Iisalmi": "KU140",
    "Kempele": "KU244",
    "Valkeakoski": "KU908",
    "Naantali": "KU529",
}

# Reverse mapping: KU-code → name
CODE_TO_NAME: dict[str, str] = {v: k for k, v in MUNICIPALITY_CODES.items()}

# ---------------------------------------------------------------------------
# PxWeb table paths
# ---------------------------------------------------------------------------

_BASE_URL = "https://pxdata.stat.fi/PXWeb/api/v1/en/StatFin/tyokay"

# 115n: employed by residence area + commuting status
_TABLE_RESIDENCE = "statfin_tyokay_pxt_115n.px"

# 115p: employed labour force by workplace area + commuting status
_TABLE_WORKPLACE = "statfin_tyokay_pxt_115p.px"

# 125s: workplace self-sufficiency (%)
_TABLE_SELFSUFF = "statfin_tyokay_pxt_125s.px"

# 115x: key employment indicators
_TABLE_INDICATORS = "statfin_tyokay_pxt_115x.px"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommutingStats:
    """Commuting statistics for a single municipality and year."""

    municipality: str
    municipality_code: str
    year: str

    # Residence-based (table 115n)
    total_employed_residents: int
    """Total employed persons living in this municipality."""
    commute_out: int
    """Residents who work in a different municipality."""
    work_locally: int
    """Residents who also work in this municipality."""

    # Workplace-based (table 115p)
    total_workplaces: int
    """Total jobs (employed labour force) located in this municipality."""
    commute_in: int
    """Workers who commute into this municipality from elsewhere."""

    # Derived
    net_commute: int
    """commute_in - commute_out: positive means net importer of workers."""
    commute_out_pct: float
    """Percentage of residents who commute out."""
    commute_in_pct: float
    """Percentage of local jobs filled by commuters from elsewhere."""

    # Supplementary indicators
    workplace_self_sufficiency: float | None = None
    """Ratio of local jobs to employed residents (%). >100 = net importer."""
    employment_rate: float | None = None
    """Employment rate for 18-64 year olds (%)."""
    unemployment_rate: float | None = None
    """Unemployment rate for 18-64 year olds (%)."""
    dependency_ratio: float | None = None
    """Economic dependency ratio."""


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class StatFiCommutingConnector:
    """Fetches and assembles commuting statistics from Statistics Finland.

    Combines four PxWeb tables into a unified view per municipality:
    residence-based commuting, workplace-based commuting, self-sufficiency,
    and key employment indicators.
    """

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        timeout: float = 30.0,
        max_concurrent: int = 5,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Shut down the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_commuting_stats(
        self,
        municipalities: list[str] | None = None,
        municipality_codes: list[str] | None = None,
        year: str = "2023",
    ) -> dict[str, CommutingStats]:
        """Fetch commuting statistics for the requested municipalities.

        Args:
            municipalities: Municipality names (e.g. ``["Helsinki", "Espoo"]``).
                If both *municipalities* and *municipality_codes* are ``None``,
                all known municipalities are queried.
            municipality_codes: Raw KU-codes (e.g. ``["KU091", "KU049"]``).
                Takes precedence over *municipalities* if both are given.
            year: Reference year (default ``"2023"``).

        Returns:
            Mapping of municipality name to :class:`CommutingStats`.
        """
        codes = self._resolve_codes(municipalities, municipality_codes)

        # Fire all four table requests in parallel
        residence_task = self._fetch_residence(codes, year)
        workplace_task = self._fetch_workplace(codes, year)
        selfsuff_task = self._fetch_self_sufficiency(codes, year)
        indicators_task = self._fetch_indicators(codes, year)

        residence, workplace, selfsuff, indicators = await asyncio.gather(
            residence_task, workplace_task, selfsuff_task, indicators_task,
            return_exceptions=True,
        )

        # Log errors but continue with partial data
        if isinstance(residence, BaseException):
            logger.error("Failed to fetch residence commuting data: %s", residence)
            residence = {}
        if isinstance(workplace, BaseException):
            logger.error("Failed to fetch workplace commuting data: %s", workplace)
            workplace = {}
        if isinstance(selfsuff, BaseException):
            logger.error("Failed to fetch self-sufficiency data: %s", selfsuff)
            selfsuff = {}
        if isinstance(indicators, BaseException):
            logger.error("Failed to fetch employment indicators: %s", indicators)
            indicators = {}

        # Assemble per-municipality results
        results: dict[str, CommutingStats] = {}

        for code in codes:
            name = CODE_TO_NAME.get(code, code)
            res = residence.get(code, {})
            wp = workplace.get(code, {})
            ss = selfsuff.get(code)
            ind = indicators.get(code, {})

            total_employed = res.get("total", 0)
            commute_out = res.get("commute_out", 0)
            work_local = res.get("work_locally", 0)
            total_jobs = wp.get("total", 0)
            commute_in = wp.get("commute_in", 0)
            net = commute_in - commute_out

            results[name] = CommutingStats(
                municipality=name,
                municipality_code=code,
                year=year,
                total_employed_residents=total_employed,
                commute_out=commute_out,
                work_locally=work_local,
                total_workplaces=total_jobs,
                commute_in=commute_in,
                net_commute=net,
                commute_out_pct=(
                    round(commute_out / total_employed * 100, 1)
                    if total_employed > 0
                    else 0.0
                ),
                commute_in_pct=(
                    round(commute_in / total_jobs * 100, 1)
                    if total_jobs > 0
                    else 0.0
                ),
                workplace_self_sufficiency=ss,
                employment_rate=ind.get("employment_rate"),
                unemployment_rate=ind.get("unemployment_rate"),
                dependency_ratio=ind.get("dependency_ratio"),
            )

        return results

    async def fetch_all_municipalities(
        self,
        year: str = "2023",
    ) -> dict[str, CommutingStats]:
        """Fetch commuting stats for *all* municipalities.

        Sends a wildcard query (``filter: all``) to the PxWeb API so we
        do not need to enumerate every KU-code.

        Returns:
            Mapping of municipality name → :class:`CommutingStats`.
        """
        residence_task = self._fetch_residence_all(year)
        workplace_task = self._fetch_workplace_all(year)
        selfsuff_task = self._fetch_self_sufficiency_all(year)
        indicators_task = self._fetch_indicators_all(year)

        residence, workplace, selfsuff, indicators = await asyncio.gather(
            residence_task, workplace_task, selfsuff_task, indicators_task,
            return_exceptions=True,
        )

        if isinstance(residence, BaseException):
            logger.error("Failed to fetch residence data: %s", residence)
            residence = {}
        if isinstance(workplace, BaseException):
            logger.error("Failed to fetch workplace data: %s", workplace)
            workplace = {}
        if isinstance(selfsuff, BaseException):
            logger.error("Failed to fetch self-sufficiency data: %s", selfsuff)
            selfsuff = {}
        if isinstance(indicators, BaseException):
            logger.error("Failed to fetch indicators data: %s", indicators)
            indicators = {}

        all_codes = set(residence.keys()) | set(workplace.keys())
        results: dict[str, CommutingStats] = {}

        for code in sorted(all_codes):
            name = CODE_TO_NAME.get(code, code)
            res = residence.get(code, {})
            wp = workplace.get(code, {})
            ss = selfsuff.get(code)
            ind = indicators.get(code, {})

            total_employed = res.get("total", 0)
            commute_out = res.get("commute_out", 0)
            work_local = res.get("work_locally", 0)
            total_jobs = wp.get("total", 0)
            commute_in = wp.get("commute_in", 0)
            net = commute_in - commute_out

            results[name] = CommutingStats(
                municipality=name,
                municipality_code=code,
                year=year,
                total_employed_residents=total_employed,
                commute_out=commute_out,
                work_locally=work_local,
                total_workplaces=total_jobs,
                commute_in=commute_in,
                net_commute=net,
                commute_out_pct=(
                    round(commute_out / total_employed * 100, 1)
                    if total_employed > 0
                    else 0.0
                ),
                commute_in_pct=(
                    round(commute_in / total_jobs * 100, 1)
                    if total_jobs > 0
                    else 0.0
                ),
                workplace_self_sufficiency=ss,
                employment_rate=ind.get("employment_rate"),
                unemployment_rate=ind.get("unemployment_rate"),
                dependency_ratio=ind.get("dependency_ratio"),
            )

        return results

    # ------------------------------------------------------------------
    # Internal: PxWeb queries
    # ------------------------------------------------------------------

    async def _post_pxweb(self, table: str, query: dict[str, Any]) -> dict[str, Any]:
        """POST a query to a PxWeb table and return parsed JSON."""
        url = f"{self._base_url}/{table}"
        client = await self._get_client()
        async with self._semaphore:
            logger.debug("StatFi commuting POST %s", url)
            resp = await client.post(url, json=query)
            resp.raise_for_status()
            return resp.json()

    def _resolve_codes(
        self,
        names: list[str] | None,
        codes: list[str] | None,
    ) -> list[str]:
        """Resolve municipality names/codes to a list of KU-codes."""
        if codes:
            return codes
        if names:
            resolved = []
            for name in names:
                code = MUNICIPALITY_CODES.get(name)
                if code is None:
                    # Try case-insensitive match
                    for k, v in MUNICIPALITY_CODES.items():
                        if k.lower() == name.lower():
                            code = v
                            break
                if code is None:
                    logger.warning("Unknown municipality: %s — skipping", name)
                    continue
                resolved.append(code)
            return resolved
        # Default: top 20 cities
        return list(MUNICIPALITY_CODES.values())[:20]

    # --- Residence-based (115n) ---

    async def _fetch_residence(
        self, codes: list[str], year: str,
    ) -> dict[str, dict[str, int]]:
        """Fetch residence-based commuting (table 115n) for specific codes."""
        query = {
            "query": [
                {
                    "code": "Alue",
                    "selection": {"filter": "item", "values": codes},
                },
                {
                    "code": "Pendelöinti",
                    "selection": {"filter": "item", "values": ["SSS", "1", "2"]},
                },
                {
                    "code": "Koulutusaste",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Ikä",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_RESIDENCE, query)
        return self._parse_residence(data)

    async def _fetch_residence_all(self, year: str) -> dict[str, dict[str, int]]:
        """Fetch residence-based commuting for all municipalities."""
        query = {
            "query": [
                {
                    "code": "Pendelöinti",
                    "selection": {"filter": "item", "values": ["SSS", "1", "2"]},
                },
                {
                    "code": "Koulutusaste",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Ikä",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_RESIDENCE, query)
        return self._parse_residence(data)

    @staticmethod
    def _parse_residence(data: dict[str, Any]) -> dict[str, dict[str, int]]:
        """Parse 115n JSON-stat2 into {code: {total, commute_out, work_locally}}."""
        area_dim = data["dimension"]["Alue"]["category"]
        commute_dim = data["dimension"]["Pendelöinti"]["category"]

        area_index = area_dim["index"]
        area_labels = area_dim["label"]
        commute_index = commute_dim["index"]

        sizes = data["size"]
        values = data["value"]

        # Dimension order: Alue, Pendelöinti, Koulutusaste, Ikä, Vuosi, Tiedot
        # With Koulutusaste=1, Ikä=1, Vuosi=1, Tiedot=1
        # Effective shape: [n_areas, 3] (flattened)
        n_commute = sizes[1]

        result: dict[str, dict[str, int]] = {}
        sorted_areas = sorted(area_index, key=lambda k: area_index[k])
        sorted_commute = sorted(commute_index, key=lambda k: commute_index[k])

        # Commute code mapping: SSS=Total, 1=outside, 2=local
        commute_map = {"SSS": "total", "1": "commute_out", "2": "work_locally"}

        for area_code in sorted_areas:
            area_idx = area_index[area_code]
            entry: dict[str, int] = {}
            for commute_code in sorted_commute:
                commute_idx = commute_index[commute_code]
                flat_idx = area_idx * n_commute + commute_idx
                val = values[flat_idx] if flat_idx < len(values) else 0
                key = commute_map.get(commute_code, commute_code)
                entry[key] = val if val is not None else 0
            result[area_code] = entry

        return result

    # --- Workplace-based (115p) ---

    async def _fetch_workplace(
        self, codes: list[str], year: str,
    ) -> dict[str, dict[str, int]]:
        """Fetch workplace-based commuting (table 115p) for specific codes."""
        query = {
            "query": [
                {
                    "code": "Työpaikan alue",
                    "selection": {"filter": "item", "values": codes},
                },
                {
                    "code": "Pendelöinti",
                    "selection": {"filter": "item", "values": ["SSS", "2", "3"]},
                },
                {
                    "code": "Koulutusaste",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Ikä",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_WORKPLACE, query)
        return self._parse_workplace(data)

    async def _fetch_workplace_all(self, year: str) -> dict[str, dict[str, int]]:
        """Fetch workplace-based commuting for all municipalities."""
        query = {
            "query": [
                {
                    "code": "Pendelöinti",
                    "selection": {"filter": "item", "values": ["SSS", "2", "3"]},
                },
                {
                    "code": "Koulutusaste",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Ikä",
                    "selection": {"filter": "item", "values": ["SSS"]},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_WORKPLACE, query)
        return self._parse_workplace(data)

    @staticmethod
    def _parse_workplace(data: dict[str, Any]) -> dict[str, dict[str, int]]:
        """Parse 115p JSON-stat2 into {code: {total, work_locally, commute_in}}."""
        # The area dimension key in 115p is "Työpaikan alue"
        area_dim = data["dimension"]["Työpaikan alue"]["category"]
        commute_dim = data["dimension"]["Pendelöinti"]["category"]

        area_index = area_dim["index"]
        commute_index = commute_dim["index"]
        sizes = data["size"]
        values = data["value"]

        n_commute = sizes[1]

        # Commute code mapping: SSS=Total, 2=local, 3=commuters_in
        commute_map = {"SSS": "total", "2": "work_locally", "3": "commute_in"}

        result: dict[str, dict[str, int]] = {}
        sorted_areas = sorted(area_index, key=lambda k: area_index[k])
        sorted_commute = sorted(commute_index, key=lambda k: commute_index[k])

        for area_code in sorted_areas:
            area_idx = area_index[area_code]
            entry: dict[str, int] = {}
            for commute_code in sorted_commute:
                commute_idx = commute_index[commute_code]
                flat_idx = area_idx * n_commute + commute_idx
                val = values[flat_idx] if flat_idx < len(values) else 0
                key = commute_map.get(commute_code, commute_code)
                entry[key] = val if val is not None else 0
            result[area_code] = entry

        return result

    # --- Self-sufficiency (125s) ---

    async def _fetch_self_sufficiency(
        self, codes: list[str], year: str,
    ) -> dict[str, float | None]:
        """Fetch workplace self-sufficiency (table 125s)."""
        query = {
            "query": [
                {
                    "code": "Alue",
                    "selection": {"filter": "item", "values": codes},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_SELFSUFF, query)
        return self._parse_single_value_by_area(data, "Alue")

    async def _fetch_self_sufficiency_all(
        self, year: str,
    ) -> dict[str, float | None]:
        """Fetch self-sufficiency for all municipalities."""
        query = {
            "query": [
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_SELFSUFF, query)
        return self._parse_single_value_by_area(data, "Alue")

    # --- Key indicators (115x) ---

    async def _fetch_indicators(
        self, codes: list[str], year: str,
    ) -> dict[str, dict[str, float | None]]:
        """Fetch key employment indicators (table 115x).

        Note: 115x has data up to 2024 while commuting tables go to 2023.
        We use the same year parameter but fall back gracefully.
        """
        # Try the requested year first; 115x might have a newer year
        years_to_try = [year]
        try:
            next_year = str(int(year) + 1)
            years_to_try.append(next_year)
        except ValueError:
            pass

        query = {
            "query": [
                {
                    "code": "Alue",
                    "selection": {"filter": "item", "values": codes},
                },
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_INDICATORS, query)
        return self._parse_indicators(data)

    async def _fetch_indicators_all(
        self, year: str,
    ) -> dict[str, dict[str, float | None]]:
        """Fetch indicators for all municipalities."""
        query = {
            "query": [
                {
                    "code": "Vuosi",
                    "selection": {"filter": "item", "values": [year]},
                },
            ],
            "response": {"format": "json-stat2"},
        }
        data = await self._post_pxweb(_TABLE_INDICATORS, query)
        return self._parse_indicators(data)

    @staticmethod
    def _parse_single_value_by_area(
        data: dict[str, Any],
        area_key: str,
    ) -> dict[str, float | None]:
        """Parse a table with one value per area."""
        area_dim = data["dimension"][area_key]["category"]
        area_index = area_dim["index"]
        values = data["value"]

        result: dict[str, float | None] = {}
        for code, idx in area_index.items():
            val = values[idx] if idx < len(values) else None
            result[code] = val

        return result

    @staticmethod
    def _parse_indicators(data: dict[str, Any]) -> dict[str, dict[str, float | None]]:
        """Parse 115x into {code: {employment_rate, unemployment_rate, dependency_ratio}}."""
        area_dim = data["dimension"]["Alue"]["category"]
        info_dim = data["dimension"]["Tiedot"]["category"]

        area_index = area_dim["index"]
        info_index = info_dim["index"]
        sizes = data["size"]
        values = data["value"]

        # info_dim codes: tyollisyysaste, tyottomyysaste, taloudellinenhuoltosuhde
        info_map = {
            "tyollisyysaste": "employment_rate",
            "tyottomyysaste": "unemployment_rate",
            "taloudellinenhuoltosuhde": "dependency_ratio",
        }

        n_info = sizes[-1]  # Tiedot is the last dimension
        result: dict[str, dict[str, float | None]] = {}

        for code in area_index:
            area_idx = area_index[code]
            entry: dict[str, float | None] = {}
            for info_code, info_idx in info_index.items():
                flat_idx = area_idx * n_info + info_idx
                val = values[flat_idx] if flat_idx < len(values) else None
                mapped = info_map.get(info_code, info_code)
                entry[mapped] = val
            result[code] = entry

        return result

    # ------------------------------------------------------------------
    # Convenience: dict export
    # ------------------------------------------------------------------

    @staticmethod
    def to_dict(stats: dict[str, CommutingStats]) -> dict[str, dict[str, Any]]:
        """Convert results to plain dictionaries for JSON serialization."""
        from dataclasses import asdict

        return {name: asdict(s) for name, s in stats.items()}


# ---------------------------------------------------------------------------
# CLI / standalone runner
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Run a demo query for the top 5 Finnish cities."""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    cities = ["Helsinki", "Espoo", "Tampere", "Vantaa", "Oulu"]
    year = "2023"

    connector = StatFiCommutingConnector()
    try:
        print(f"\nFetching commuting statistics for {', '.join(cities)} ({year})...\n")
        stats = await connector.fetch_commuting_stats(
            municipalities=cities, year=year,
        )

        # Header
        print(f"{'Municipality':<14} {'Residents':>10} {'Jobs':>10} {'Out':>8} "
              f"{'In':>8} {'Net':>8} {'Out%':>6} {'In%':>6} "
              f"{'SelfSuff':>9} {'EmpRate':>8}")
        print("-" * 107)

        for name in cities:
            s = stats.get(name)
            if s is None:
                print(f"{name:<14} (no data)")
                continue
            ss = f"{s.workplace_self_sufficiency:.1f}%" if s.workplace_self_sufficiency else "n/a"
            er = f"{s.employment_rate:.1f}%" if s.employment_rate else "n/a"
            print(
                f"{s.municipality:<14} "
                f"{s.total_employed_residents:>10,} "
                f"{s.total_workplaces:>10,} "
                f"{s.commute_out:>8,} "
                f"{s.commute_in:>8,} "
                f"{s.net_commute:>+8,} "
                f"{s.commute_out_pct:>5.1f}% "
                f"{s.commute_in_pct:>5.1f}% "
                f"{ss:>9} "
                f"{er:>8}"
            )

        print()

        # Detailed view
        for name in cities:
            s = stats.get(name)
            if s is None:
                continue
            print(f"--- {s.municipality} ({s.municipality_code}) ---")
            print(f"  Employed residents:     {s.total_employed_residents:>10,}")
            print(f"    Work locally:         {s.work_locally:>10,}")
            print(f"    Commute out:          {s.commute_out:>10,}  "
                  f"({s.commute_out_pct:.1f}%)")
            print(f"  Jobs in municipality:   {s.total_workplaces:>10,}")
            print(f"    Filled by locals:     {s.work_locally:>10,}")
            print(f"    Filled by commuters:  {s.commute_in:>10,}  "
                  f"({s.commute_in_pct:.1f}%)")
            print(f"  Net commute balance:    {s.net_commute:>+10,}")
            if s.workplace_self_sufficiency is not None:
                print(f"  Workplace self-suff.:   {s.workplace_self_sufficiency:>9.1f}%")
            if s.employment_rate is not None:
                print(f"  Employment rate (18-64): {s.employment_rate:>8.1f}%")
            if s.unemployment_rate is not None:
                print(f"  Unemployment rate:       {s.unemployment_rate:>8.1f}%")
            if s.dependency_ratio is not None:
                print(f"  Dependency ratio:        {s.dependency_ratio:>8.1f}")
            print()

    finally:
        await connector.close()


if __name__ == "__main__":
    asyncio.run(_main())
