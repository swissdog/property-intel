"""Oikotie.fi listing connector.

Fetches active real estate listings from Oikotie's consumer API.
Tokens are acquired from the homepage <meta> tags on each session.

Usage::

    from jarvis_property_intel.connectors.oikotie.config import OikotieConfig
    from jarvis_property_intel.connectors.oikotie.connector import OikotieConnector

    connector = OikotieConnector(OikotieConfig())
    results = await connector.fetch_listings(locations='[[64,6,"Helsinki"]]')
    for raw in results:
        records = connector.normalize(raw)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from ..base import NormalizedRecord, RawFetchResult
from .config import OikotieConfig

logger = logging.getLogger(__name__)

# Location IDs for major cities
LOCATION_IDS: dict[str, list] = {
    # Tier 1 — Core market
    "tampere": [71, 6, "Tampere"],
    "turku": [79, 6, "Turku"],
    "helsinki": [64, 6, "Helsinki"],
    "vantaa": [82, 6, "Vantaa"],
    "espoo": [39, 6, "Espoo"],
    # Tier 1 — Tampereen kehyskunnat
    "pirkkala": [604, 6, "Pirkkala"],
    "ylöjärvi": [980, 6, "Ylöjärvi"],
    "nokia": [536, 6, "Nokia"],
    "kangasala": [211, 6, "Kangasala"],
    # Tier 2 — Yliopisto- ja aluekeskukset
    "oulu": [54, 6, "Oulu"],
    "jyväskylä": [43, 6, "Jyväskylä"],
    "kuopio": [46, 6, "Kuopio"],
    "rovaniemi": [698, 6, "Rovaniemi"],
    "lahti": [47, 6, "Lahti"],
    # Tier 3 — Kassavirta-kaupungit
    "kotka": [285, 6, "Kotka"],
    "kajaani": [205, 6, "Kajaani"],
    "kouvola": [286, 6, "Kouvola"],
    "pori": [609, 6, "Pori"],
    "lappeenranta": [405, 6, "Lappeenranta"],
}

# Building type mapping
BUILDING_TYPES: dict[int, str] = {
    1: "apartment_unit",
    2: "rowhouse_unit",
    4: "detached_house",
    8: "leisure",
    32: "semi_detached",
    64: "pair_house",
    128: "other",
    256: "loft_house",
    512: "wooden_apartment",
}


class OikotieConnector:
    """Connector for Oikotie.fi listing data.

    Implements the ListingSourceConnector protocol.
    """

    source_id: str = "oikotie"

    def __init__(self, config: OikotieConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = asyncio.Semaphore(config.max_concurrent)
        # Session tokens
        self._ota_token: str | None = None
        self._ota_loaded: str | None = None
        self._ota_cuid: str | None = None
        self._token_acquired_at: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._config.timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://asunnot.oikotie.fi/myytavat-asunnot",
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
    # Token acquisition
    # ------------------------------------------------------------------

    async def _acquire_tokens(self) -> bool:
        """Fetch the Oikotie homepage and extract OTA tokens from meta tags.

        Returns True if tokens were successfully acquired.
        """
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self._config.base_url}/myytavat-asunnot",
                headers={"Accept": "text/html"},
            )
            resp.raise_for_status()
            html = resp.text

            # Extract meta tags
            token_match = re.search(
                r'<meta\s+name="api-token"\s+content="([^"]+)"', html
            )
            loaded_match = re.search(
                r'<meta\s+name="loaded"\s+content="([^"]+)"', html
            )
            cuid_match = re.search(
                r'<meta\s+name="cuid"\s+content="([^"]+)"', html
            )

            if not all([token_match, loaded_match, cuid_match]):
                logger.error(
                    "Failed to extract OTA tokens from Oikotie homepage. "
                    "token=%s loaded=%s cuid=%s",
                    bool(token_match),
                    bool(loaded_match),
                    bool(cuid_match),
                )
                return False

            self._ota_token = token_match.group(1)
            self._ota_loaded = loaded_match.group(1)
            self._ota_cuid = cuid_match.group(1)
            self._token_acquired_at = datetime.now(tz=UTC)

            logger.info(
                "Acquired Oikotie OTA tokens (loaded=%s)", self._ota_loaded
            )
            return True

        except Exception:
            logger.exception("Failed to acquire Oikotie OTA tokens")
            return False

    def _auth_headers(self) -> dict[str, str]:
        """Return OTA authentication headers."""
        if not all([self._ota_token, self._ota_loaded, self._ota_cuid]):
            raise RuntimeError("OTA tokens not acquired — call _acquire_tokens() first")
        return {
            "OTA-token": self._ota_token,
            "OTA-loaded": self._ota_loaded,
            "OTA-cuid": self._ota_cuid,
        }

    async def _ensure_tokens(self) -> bool:
        """Acquire tokens if not present or older than 10 minutes."""
        if self._ota_token is None or (
            self._token_acquired_at
            and (datetime.now(tz=UTC) - self._token_acquired_at).total_seconds()
            > 600
        ):
            return await self._acquire_tokens()
        return True

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if tokens can be acquired and API responds."""
        if not await self._ensure_tokens():
            return False
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self._config.base_url}/api/cards",
                params={
                    "cardType": self._config.card_type,
                    "limit": 1,
                    "offset": 0,
                    "sortBy": "published_desc",
                },
                headers=self._auth_headers(),
            )
            return resp.status_code == 200
        except Exception:
            logger.exception("Oikotie health check failed")
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Generic fetch — delegates to fetch_listings."""
        return await self.fetch_listings(**kwargs)

    async def fetch_listings(
        self,
        locations: str | None = None,
        card_type: int | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        size_min: float | None = None,
        building_types: list[int] | None = None,
        max_pages: int | None = None,
        **params: Any,
    ) -> list[RawFetchResult]:
        """Fetch listing cards from Oikotie search API.

        Args:
            locations: JSON-encoded location filter, e.g. '[[64,6,"Helsinki"]]'.
            card_type: Listing category (default: 100 = apartments for sale).
            price_min: Minimum price filter.
            price_max: Maximum price filter.
            size_min: Minimum size in m2.
            building_types: List of building type codes to filter.
            max_pages: Maximum pages to fetch (overrides config).

        Returns:
            List of RawFetchResult, one per page fetched.
        """
        if not await self._ensure_tokens():
            logger.error("Cannot fetch: OTA token acquisition failed")
            return []

        client = await self._get_client()
        ct = card_type or self._config.card_type
        locs = locations or self._config.default_locations
        pages = max_pages or self._config.max_pages

        results: list[RawFetchResult] = []
        offset = 0

        for page in range(pages):
            query_params: dict[str, Any] = {
                "cardType": ct,
                "limit": self._config.page_size,
                "offset": offset,
                "sortBy": "published_desc",
                "locations": locs,
            }

            if price_min is not None:
                query_params["price[min]"] = price_min
            if price_max is not None:
                query_params["price[max]"] = price_max
            if size_min is not None:
                query_params["size[min]"] = size_min
            if building_types:
                for bt in building_types:
                    query_params.setdefault("buildingType[]", []).append(bt)

            async with self._rate_limiter:
                logger.debug(
                    "Oikotie: fetching page %d (offset=%d)", page + 1, offset
                )
                try:
                    resp = await client.get(
                        f"{self._config.base_url}/api/cards",
                        params=query_params,
                        headers=self._auth_headers(),
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 401:
                        logger.warning("Token expired, re-acquiring...")
                        if await self._acquire_tokens():
                            resp = await client.get(
                                f"{self._config.base_url}/api/cards",
                                params=query_params,
                                headers=self._auth_headers(),
                            )
                            resp.raise_for_status()
                        else:
                            logger.error("Token re-acquisition failed")
                            break
                    else:
                        raise

            fetched_at = datetime.now(tz=UTC)
            body = resp.content

            results.append(
                RawFetchResult(
                    source_id=self.source_id,
                    fetched_at=fetched_at,
                    raw_content=body,
                    content_type="application/json",
                    parse_version="oikotie_cards_v1",
                    url=str(resp.url),
                )
            )

            # Check if more pages available
            try:
                data = resp.json()
            except Exception:
                break

            cards = data.get("cards", [])
            total = data.get("found", 0)

            if not cards:
                break

            offset += len(cards)
            if offset >= total:
                break

            # Rate limiting — be polite
            await asyncio.sleep(self._config.request_delay)

        logger.info(
            "Oikotie: fetched %d page(s), %d total results available",
            len(results),
            total if results else 0,
        )
        return results

    async def fetch_detail(self, card_id: int) -> RawFetchResult | None:
        """Fetch full detail for a single listing card."""
        if not await self._ensure_tokens():
            return None

        client = await self._get_client()
        async with self._rate_limiter:
            try:
                resp = await client.get(
                    f"{self._config.base_url}/api/card/{card_id}",
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
            except Exception:
                logger.exception("Failed to fetch detail for card %d", card_id)
                return None

        await asyncio.sleep(self._config.request_delay)

        return RawFetchResult(
            source_id=self.source_id,
            fetched_at=datetime.now(tz=UTC),
            raw_content=resp.content,
            content_type="application/json",
            parse_version="oikotie_card_detail_v1",
            url=str(resp.url),
        )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse Oikotie API response into normalized listing records."""
        if raw.parse_version == "oikotie_card_detail_v1":
            return self._normalize_detail(raw)
        return self._normalize_cards(raw)

    def _normalize_cards(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Normalize a /api/cards search response."""
        try:
            data = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode Oikotie response from %s", raw.url)
            return []

        records: list[NormalizedRecord] = []

        for card in data.get("cards", []):
            try:
                record = self._card_to_record(card, raw.fetched_at)
                if record:
                    records.append(record)
            except Exception:
                logger.exception(
                    "Failed to normalize Oikotie card %s", card.get("id")
                )

        logger.info("Oikotie: normalized %d cards from %s", len(records), raw.url)
        return records

    def _normalize_detail(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Normalize a /api/card/{id} detail response."""
        try:
            card = json.loads(raw.raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Failed to decode Oikotie detail from %s", raw.url)
            return []

        record = self._detail_to_record(card, raw.fetched_at)
        return [record] if record else []

    def _card_to_record(
        self, card: dict[str, Any], fetched_at: datetime
    ) -> NormalizedRecord | None:
        """Convert a search result card to a NormalizedRecord."""
        card_id = card.get("id")
        if not card_id:
            return None

        building = card.get("buildingData", {}) or {}
        coords = card.get("coordinates", {}) or {}

        # Parse price from formatted string like "238 000 EUR" or "238000"
        price = self._parse_price(card.get("price"))

        # Parse size
        size = card.get("size")
        if isinstance(size, str):
            size = self._parse_float(size)

        # Determine asset type from building type
        bt = building.get("buildingType", "")
        asset_type = self._map_building_type(bt)

        # Build address
        address_parts = []
        if building.get("address"):
            address_parts.append(building["address"])

        data: dict[str, Any] = {
            "oikotie_id": card_id,
            "url": card.get("url", ""),
            "status": "active",
            "asking_price": price,
            "living_area_m2": size,
            "address": building.get("address"),
            "district": building.get("district"),
            "city": building.get("city"),
            "year_built": self._parse_int(building.get("year")),
            "floor": building.get("floor"),
            "floor_count": building.get("floorCount"),
            "building_type": bt,
            "asset_type": asset_type,
            "lat": coords.get("latitude"),
            "lon": coords.get("longitude"),
            "rooms": card.get("rooms"),
            "room_configuration": card.get("roomConfiguration"),
            "description": card.get("description"),
            "published": card.get("published"),
            "visits": card.get("visits"),
            "visits_weekly": card.get("visits_weekly"),
            "price_changed": card.get("priceChanged"),
            "new_development": card.get("newDevelopment"),
        }

        # Add brand/realtor info
        brand = card.get("brand", {})
        if brand:
            data["agency_name"] = brand.get("name")
            data["agency_id"] = brand.get("id")

        return NormalizedRecord(
            source_id=self.source_id,
            record_type="listing",
            source_record_id=str(card_id),
            data=data,
            fetched_at=fetched_at,
        )

    def _detail_to_record(
        self, card: dict[str, Any], fetched_at: datetime
    ) -> NormalizedRecord | None:
        """Convert a detail card to a NormalizedRecord with full data."""
        card_id = card.get("cardId") or card.get("id")
        if not card_id:
            return None

        building = card.get("building", {}) or card.get("buildingData", {}) or {}
        coords = card.get("coordinates", {}) or {}
        ad_data = card.get("adData", {}) or {}
        price_data = card.get("priceData", {}) or {}
        address = card.get("address", {}) or {}

        # Use numeric price from priceData
        price = price_data.get("price") or self._parse_price(card.get("price"))

        # Size and rooms from adData (detail) or top-level (search)
        size = ad_data.get("size") or card.get("size")
        if isinstance(size, str):
            size = self._parse_float(size)
        rooms = ad_data.get("rooms") or card.get("rooms")

        bt = (
            ad_data.get("buildingOverrideBuildingType")
            or building.get("buildingType", "")
        )
        asset_type = self._map_building_type(bt)

        # Extract postal code from address hierarchy
        postal_code = None
        zip_code = address.get("zipCode", {})
        if isinstance(zip_code, dict):
            postal_code = zip_code.get("name") or zip_code.get("value")
        elif isinstance(zip_code, str):
            postal_code = zip_code

        # Address from address.formattedAddress or building
        formatted_address = address.get("formattedAddress") or building.get("address")

        # City from address hierarchy
        city_obj = address.get("city", {})
        city = city_obj.get("name") if isinstance(city_obj, dict) else building.get("city")

        # District from address.districts or building
        districts = address.get("districts")
        district = None
        if isinstance(districts, list) and districts:
            first_d = districts[0]
            district = first_d.get("name") if isinstance(first_d, dict) else str(first_d)
        if not district:
            district_obj = address.get("district", {})
            district = district_obj.get("name") if isinstance(district_obj, dict) else building.get("district")

        data: dict[str, Any] = {
            "oikotie_id": card_id,
            "url": card.get("url", ""),
            "status": "active",
            "asking_price": price,
            "living_area_m2": size,
            "address": formatted_address,
            "postal_code": postal_code,
            "district": district,
            "city": city,
            "municipality": city,
            "year_built": self._parse_int(
                ad_data.get("buildYearInfo") or ad_data.get("buildingOverrideBuildYear")
            ),
            "floor": ad_data.get("floor") or building.get("floor"),
            "floor_count": ad_data.get("floorCount") or ad_data.get("buildingOverrideFloors"),
            "building_type": bt,
            "asset_type": asset_type,
            "lat": self._parse_float(coords.get("latitude")),
            "lon": self._parse_float(coords.get("longitude")),
            "rooms": rooms,
            "room_configuration": ad_data.get("roomConfiguration") or card.get("roomConfiguration"),
            "description": card.get("description"),
            "published": card.get("published"),
            # Pricing — buyer's full out-the-door cost = price + share_of_liabilities.
            # priceData.price is the asking; debt_free_price equals price for unencumbered.
            # Oikotie sometimes ships fees as strings like "6.00 e / kk" — parse robustly.
            "share_of_liabilities_eur": self._parse_price(price_data.get("shareOfLiabilities")),
            "debt_free_price": (
                (self._parse_price(price_data.get("price")) or 0)
                + (self._parse_price(price_data.get("shareOfLiabilities")) or 0)
                if price_data.get("price") is not None else None
            ),
            # Recurring monthly fees (yhtiövastike, rahoitusvastike, vesi, autopaikka, sauna)
            "maintenance_fee_eur": self._parse_price(
                ad_data.get("maintenanceFee") or ad_data.get("managementCharge")
            ),
            "financial_fee_eur": self._parse_price(ad_data.get("financialFee")),
            "water_fee_eur": self._parse_price(ad_data.get("waterFee")),
            "parking_fee_eur": self._parse_price(ad_data.get("parkingFee")),
            "sauna_fee_eur": self._parse_price(ad_data.get("saunaCharge")),
            # Apartment + building characteristics
            "apartment_condition_code": self._parse_int(ad_data.get("apartmentCondition")),
            "heating_method": (
                str(ad_data.get("heatingInfo"))[:80]
                if ad_data.get("heatingInfo") else None
            ),
            "heating_method_code": (
                str((ad_data.get("heatingMethods") or [None])[0])
                if isinstance(ad_data.get("heatingMethods"), list)
                and ad_data.get("heatingMethods")
                else None
            ),
            "building_material": (
                str(ad_data.get("buildingOverrideBuildingMaterialInfo"))[:40]
                if ad_data.get("buildingOverrideBuildingMaterialInfo") else None
            ),
            "has_lift": bool(ad_data.get("buildingOverrideLift")) if ad_data.get("buildingOverrideLift") is not None else None,
            "has_sauna": bool(ad_data.get("buildingOverrideSauna")) if ad_data.get("buildingOverrideSauna") is not None else None,
            "lot_ownership_code": self._parse_int(ad_data.get("buildingOverrideLotOwnership")),
            "energy_class_full": self._parse_energy_class(
                ad_data.get("buildingOverrideEnergyClass")
                or ad_data.get("energyClassInfo")
            ),
            # Free-text fields (kept for json_blob)
            "lot_area": ad_data.get("lotSize") or ad_data.get("buildingOverrideLotSize"),
            "lot_ownership_info": ad_data.get("lotOwnershipInfo"),
            "renovation_info": ad_data.get("renovationInfo"),
            "future_renovations": ad_data.get("renovationFutureInfo"),
            "condition_info": ad_data.get("conditionInfo"),
            "condition_inspection_info": ad_data.get("conditionInspectionInfo"),
            "balcony_info": ad_data.get("balconyInfo"),
            "parking_info": ad_data.get("parkingSpaceInfo"),
            "housing_company_name": ad_data.get("housingCompanyName"),
            "description_text": ad_data.get("description"),
        }

        return NormalizedRecord(
            source_id=self.source_id,
            record_type="listing",
            source_record_id=str(card_id),
            data=data,
            fetched_at=fetched_at,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_energy_class(value: Any) -> str | None:
        """Extract Finnish energy class code (e.g. 'C2018') from a free-text label.

        Oikotie ships the full label in adData.buildingOverrideEnergyClass:
        'Energialuokka: C2018, Energiatodistuksen voimassaoloaika: 16.12.2034'.
        Returns 'C2018' (or 'C' if no year suffix). None if no class found.
        """
        if not value or not isinstance(value, str):
            return None
        # Strip the "Energialuokka:" prefix (case-insensitive) so its 'E' doesn't match.
        stripped = re.sub(r"energialuokka\s*:?\s*", "", value, flags=re.IGNORECASE)
        # Match a single A-G class letter optionally followed by year suffix,
        # bounded so we don't match the 'E' in 'Energiatodistuksen', etc.
        m = re.search(r"\b([A-G])\s*(20\d{2})?(?=[\s,.]|$)", stripped, flags=re.IGNORECASE)
        if not m:
            return None
        code, year = m.group(1).upper(), m.group(2)
        return f"{code}{year}" if year else code

    @staticmethod
    def _parse_price(value: Any) -> float | None:
        """Parse price from various formats."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove spaces, currency, non-numeric chars except dots
            cleaned = re.sub(r"[^\d.,]", "", value.replace(" ", ""))
            cleaned = cleaned.replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d.,]", "", value).replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @staticmethod
    def _parse_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d]", "", value)
            try:
                return int(cleaned) if cleaned else None
            except ValueError:
                return None
        return None

    @staticmethod
    def _map_building_type(bt: str | int) -> str:
        """Map Oikotie building type to our asset_type enum."""
        # Handle integer building type codes
        if isinstance(bt, int) or (isinstance(bt, str) and bt.isdigit()):
            int_bt = int(bt)
            return BUILDING_TYPES.get(int_bt, "unknown")
        # Handle string names
        bt_str = str(bt).lower()
        mapping = {
            "kerrostalo": "apartment_unit",
            "rivitalo": "rowhouse_unit",
            "omakotitalo": "detached_house",
            "erillistalo": "detached_house",
            "paritalo": "detached_house",
            "luhtitalo": "apartment_unit",
            "puutalo-osake": "apartment_unit",
        }
        for key, val in mapping.items():
            if key in bt_str:
                return val
        return "unknown"
