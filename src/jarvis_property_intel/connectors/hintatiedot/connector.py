"""Asuntojen hintatiedot.fi — realized transaction price connector.

Scrapes completed property sale prices from the KVKL (Finnish Real Estate
Federation) public service at asuntojen.hintatiedot.fi.

Data covers the last 12 months of transactions reported by major Finnish
real estate agencies (Kiinteistömaailma, OP Koti, Huoneistokeskus, Aktia,
RE/MAX, Sp-Koti).

Fields per transaction:
    - neighborhood (kaupunginosa)
    - room_config (huoneisto)
    - building_type (kt=kerrostalo, rt=rivitalo, ok=omakotitalo)
    - living_area_m2
    - debt_free_price (velaton hinta)
    - price_per_m2 (€/m²)
    - year_built
    - floor (e.g. "2/5")
    - elevator (on/ei)
    - condition (hyvä/tyyd./huono)
    - lot_type (oma/vuokra)
    - energy_class (A-G)

Usage::

    from jarvis_property_intel.connectors.hintatiedot import HintatiedotConfig, HintatiedotConnector

    connector = HintatiedotConnector(HintatiedotConfig())
    records = await connector.fetch_city("Tampere")
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from ..base import NormalizedRecord
from .config import HintatiedotConfig

logger = logging.getLogger(__name__)

# Building type normalization
BUILDING_TYPES = {"kt": "apartment", "rt": "rowhouse", "ok": "detached"}


class _TableParser(HTMLParser):
    """Parse the HTML transaction table from hintatiedot.fi."""

    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._current_row: list[str] = []
        self._rows: list[list[str]] = []
        self._section = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "td":
            self._in_td = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._in_td = False
        elif tag == "tr":
            row = self._current_row
            self._current_row = []
            if len(row) >= 10:
                self._rows.append(row)

    def handle_data(self, data: str) -> None:
        if self._in_td:
            text = data.strip()
            if text:
                self._current_row.append(text)

    @property
    def rows(self) -> list[list[str]]:
        return self._rows


def _parse_float(s: str) -> float | None:
    """Parse Finnish-format float (comma decimal separator)."""
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except (ValueError, AttributeError):
        return None


def _parse_int(s: str) -> int | None:
    """Parse integer, ignoring non-numeric chars."""
    cleaned = re.sub(r"[^\d]", "", s)
    return int(cleaned) if cleaned else None


class HintatiedotConnector:
    """Connector for asuntojen.hintatiedot.fi transaction data."""

    source_id: str = "hintatiedot_kvkl"

    def __init__(self, config: HintatiedotConfig | None = None) -> None:
        self._config = config or HintatiedotConfig()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout,
                follow_redirects=True,
                headers={"User-Agent": "JARVIS-PropertyIntel/1.0"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_city(
        self,
        city: str,
        max_pages: int | None = None,
    ) -> list[NormalizedRecord]:
        """Fetch all transactions for a city across all pages.

        Args:
            city: City name (e.g. "Tampere", "Helsinki").
            max_pages: Override max pages (default from config).

        Returns:
            List of NormalizedRecord with record_type="transaction".
        """
        client = await self._get_client()
        limit = max_pages or self._config.max_pages
        all_records: list[NormalizedRecord] = []
        page = 0

        while page < limit:
            params: dict[str, str] = {
                "c": city,
                "cr": "1",
                "renderType": "renderTypeTable",
                "search": "1",
                "sf": "0",
                "so": "a",
            }
            if page > 0:
                params["z"] = str(page + 1)

            try:
                resp = await client.get(
                    f"{self._config.base_url}/haku/",
                    params=params,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error("Hintatiedot fetch failed for %s page %d: %s", city, page, e)
                break

            parser = _TableParser()
            parser.feed(resp.text)
            rows = parser.rows

            if not rows:
                break

            for row in rows:
                record = self._normalize_row(row, city)
                if record:
                    all_records.append(record)

            has_next = "seuraava sivu" in resp.text
            if not has_next:
                break

            page += 1
            if page < limit:
                await asyncio.sleep(self._config.delay_between_requests)

        logger.info(
            "Hintatiedot: %s → %d transactions (%d pages)",
            city, len(all_records), page + 1,
        )
        return all_records

    def _normalize_row(
        self, row: list[str], city: str
    ) -> NormalizedRecord | None:
        """Convert a parsed HTML row to a NormalizedRecord.

        Applies sanity checks to detect and correct swapped columns.
        The expected pattern from hintatiedot.fi is:
          [neighborhood, room_config, building_type, living_area_m2,
           debt_free_price, price_per_m2, year_built, floor, elevator,
           condition, lot_type, energy_class]
        """
        # Pad to 12 columns
        while len(row) < 12:
            row.append("")

        neighborhood = row[0]
        room_config = row[1]
        building_type_raw = row[2].strip().lower()
        living_area_m2 = _parse_float(row[3])
        debt_free_price = _parse_float(row[4])
        price_per_m2 = _parse_float(row[5])
        year_built = _parse_int(row[6])
        floor = row[7].strip()
        elevator = row[8].strip().lower()
        condition = row[9].strip()
        lot_type = row[10].strip()
        energy_class = row[11].strip()

        if debt_free_price is None or debt_free_price <= 0:
            return None

        # ── Sanity checks: detect and fix swapped columns ──
        # Normal: living_area 10-500 m², debt_free_price 20k-5M €
        # Swapped: living_area has a price value, debt_free_price has an area value
        if living_area_m2 is not None and debt_free_price is not None:
            area_looks_like_price = living_area_m2 > 10_000
            price_looks_like_area = debt_free_price < 1_000

            if area_looks_like_price and price_looks_like_area:
                # Clearly swapped — swap them back
                logger.debug(
                    "Swapped columns detected for %s/%s: area=%.0f price=%.0f → fixing",
                    city, neighborhood, living_area_m2, debt_free_price,
                )
                living_area_m2, debt_free_price = debt_free_price, living_area_m2

            elif area_looks_like_price and not price_looks_like_area:
                # living_area has a big number, price also big — likely
                # price_per_m2 in price field, debt_free_price in area field
                if price_per_m2 and debt_free_price < price_per_m2 * 5:
                    # debt_free_price is close to price_per_m2 → it IS price_per_m2
                    logger.debug(
                        "Column shift detected for %s/%s: repricing", city, neighborhood,
                    )
                    living_area_m2_corrected = living_area_m2 / debt_free_price if debt_free_price > 0 else None
                    if living_area_m2_corrected and 10 < living_area_m2_corrected < 500:
                        debt_free_price = living_area_m2
                        living_area_m2 = round(living_area_m2_corrected, 1)

        # Final validation: reject impossible values
        if living_area_m2 is not None and (living_area_m2 < 5 or living_area_m2 > 1000):
            logger.debug("Skipping row with implausible area %.1f m²: %s/%s", living_area_m2, city, neighborhood)
            return None
        if debt_free_price < 5_000 or debt_free_price > 20_000_000:
            logger.debug("Skipping row with implausible price %.0f: %s/%s", debt_free_price, city, neighborhood)
            return None

        # Recalculate price_per_m2 from validated values (don't trust the source column)
        if living_area_m2 and living_area_m2 > 0:
            price_per_m2 = round(debt_free_price / living_area_m2, 1)

        building_type = BUILDING_TYPES.get(building_type_raw, building_type_raw)

        # Stable record_id: based on location + physical characteristics, NOT price
        # This ensures price updates don't create duplicate records
        import hashlib
        id_source = f"ht|{city}|{neighborhood}|{room_config}|{building_type}|{living_area_m2}|{year_built}|{floor}"
        record_id = "ht_" + hashlib.sha256(id_source.encode()).hexdigest()[:20]

        return NormalizedRecord(
            source_id=self.source_id,
            record_type="transaction",
            source_record_id=record_id,
            data={
                "city": city,
                "neighborhood": neighborhood,
                "room_config": room_config,
                "building_type": building_type,
                "living_area_m2": living_area_m2,
                "debt_free_price": debt_free_price,
                "price_per_m2": price_per_m2,
                "year_built": year_built,
                "floor": floor,
                "elevator": elevator in ("on", "kyllä"),
                "condition": condition,
                "lot_type": lot_type,
                "energy_class": energy_class,
            },
        )
