"""PRH Business Register — company search via YTJ open data API.

The PRH (Patentti- ja rekisterihallitus) publishes Finnish business
register data through an open REST API at:

    https://avoindata.prh.fi/opendata-ytj-api/v3/companies

Supported search parameters:
    - ``name`` — company name (partial match)
    - ``businessId`` — exact Y-tunnus lookup
    - ``location`` — municipality name filter (e.g. "Helsinki")
    - ``companyRegistrationFrom`` / ``companyRegistrationTo`` — date filters

Note: The ``businessLineCode`` parameter is listed in the API docs but
does **not** actually filter results as of 2026-03.  To find companies
by TOL code, use name search and filter client-side.

Usage::

    from jarvis_property_intel.connectors.prh.business_search import PRHBusinessSearch

    prh = PRHBusinessSearch()

    # Search by name
    results = await prh.search(name="Aimo Park")

    # Search parking operators in Helsinki
    results = await prh.search(name="pysäköinti", location="Helsinki")

    # Lookup by business ID
    results = await prh.search(business_id="2208141-1")

    await prh.close()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    """Parsed company record from the PRH register."""

    business_id: str
    name: str
    auxiliary_names: list[str]
    company_form: str
    registration_date: str
    business_line_code: str | None
    business_line_description: str | None
    street_address: str | None
    post_code: str | None
    city: str | None
    status: str


@dataclass
class PRHBusinessSearch:
    """Search the PRH (YTJ) open data API for Finnish companies.

    Provides name search, business-ID lookup, and location-filtered
    queries with automatic pagination.
    """

    base_url: str = _BASE_URL
    timeout: float = 30.0
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
                headers={"Accept": "application/json"},
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

    async def health_check(self) -> bool:
        """Return ``True`` if the PRH API responds to a minimal query."""
        try:
            client = await self._get_client()
            resp = await client.get(
                self.base_url, params={"name": "test", "maxResults": "1"}
            )
            return resp.status_code < 400
        except Exception:
            logger.exception("PRH health-check failed")
            return False

    async def search(
        self,
        *,
        name: str | None = None,
        business_id: str | None = None,
        location: str | None = None,
        registration_from: str | None = None,
        registration_to: str | None = None,
        business_line_code: str | None = None,
        max_results: int = 100,
    ) -> list[CompanyRecord]:
        """Search for companies in the PRH register.

        At least one of ``name`` or ``business_id`` must be provided.

        Args:
            name: Company name (partial match).
            business_id: Exact Y-tunnus (e.g. ``"2208141-1"``).
            location: Municipality name filter (e.g. ``"Helsinki"``).
            registration_from: Registration date lower bound (YYYY-MM-DD).
            registration_to: Registration date upper bound (YYYY-MM-DD).
            business_line_code: TOL code to filter results client-side
                (the API parameter does not work, so we filter after fetch).
            max_results: Maximum number of results to return.

        Returns:
            List of :class:`CompanyRecord` instances.
        """
        if not name and not business_id:
            raise ValueError("At least one of 'name' or 'business_id' must be provided")

        params: dict[str, str] = {"maxResults": str(min(max_results, 100))}
        if name:
            params["name"] = name
        if business_id:
            params["businessId"] = business_id
        if location:
            params["location"] = location
        if registration_from:
            params["companyRegistrationFrom"] = registration_from
        if registration_to:
            params["companyRegistrationTo"] = registration_to

        raw = await self._fetch(params)
        records = [self._parse_company(c) for c in raw]

        # Client-side TOL code filtering (API param doesn't work)
        if business_line_code:
            records = [
                r for r in records
                if r.business_line_code and r.business_line_code.startswith(business_line_code)
            ]

        return records

    async def search_dict(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Same as :meth:`search` but returns plain dicts."""
        records = await self.search(**kwargs)
        return [
            {
                "business_id": r.business_id,
                "name": r.name,
                "auxiliary_names": r.auxiliary_names,
                "company_form": r.company_form,
                "registration_date": r.registration_date,
                "business_line_code": r.business_line_code,
                "business_line_description": r.business_line_description,
                "street_address": r.street_address,
                "post_code": r.post_code,
                "city": r.city,
                "status": r.status,
            }
            for r in records
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """Execute a single GET request against the PRH API."""
        client = await self._get_client()
        async with self._rate_limiter:
            logger.debug("PRH GET %s params=%s", self.base_url, params)
            resp = await client.get(self.base_url, params=params)
            resp.raise_for_status()

        data = resp.json()
        return data.get("companies", [])

    @staticmethod
    def _parse_company(raw: dict[str, Any]) -> CompanyRecord:
        """Parse a single company JSON object into a CompanyRecord."""

        # Business ID
        bid = raw.get("businessId", {})
        business_id = bid.get("value", "")
        registration_date = bid.get("registrationDate", "")

        # Current company name (type=1, no endDate, or latest)
        names = raw.get("names", [])
        current_name = ""
        for n in names:
            if n.get("type") == "1" and not n.get("endDate"):
                current_name = n.get("name", "")
                break
        if not current_name:
            # Fallback: take the first type=1 name
            for n in names:
                if n.get("type") == "1":
                    current_name = n.get("name", "")
                    break

        # Auxiliary names (type=3)
        aux_names = [
            n["name"] for n in names
            if n.get("type") == "3" and not n.get("endDate")
        ]

        # Company form
        company_form = ""
        for cf in raw.get("companyForms", []):
            if not cf.get("endDate"):
                descs = cf.get("descriptions", [])
                for d in descs:
                    if d.get("languageCode") == "3":  # English
                        company_form = d.get("description", "")
                        break
                if not company_form:
                    for d in descs:
                        if d.get("languageCode") == "1":  # Finnish
                            company_form = d.get("description", "")
                            break
                break

        # Main business line
        bl = raw.get("mainBusinessLine", {})
        bl_code = bl.get("type")
        bl_desc = None
        for d in bl.get("descriptions", []):
            if d.get("languageCode") == "3":
                bl_desc = d.get("description")
                break
        if bl_desc is None:
            for d in bl.get("descriptions", []):
                if d.get("languageCode") == "1":
                    bl_desc = d.get("description")
                    break

        # Address (type=1 = street address)
        street_address = None
        post_code = None
        city = None
        for addr in raw.get("addresses", []):
            if addr.get("type") == 1:
                street_parts = [addr.get("street", "")]
                if addr.get("buildingNumber"):
                    street_parts.append(addr["buildingNumber"])
                if addr.get("entrance"):
                    street_parts.append(addr["entrance"])
                street_address = " ".join(p for p in street_parts if p).strip()
                post_code = addr.get("postCode")
                # City from post offices
                for po in addr.get("postOffices", []):
                    if po.get("languageCode") == "1":
                        city = po.get("city")
                        break
                break

        # Registration status (from registeredEntries, register=1 = kaupparekisteri)
        status = "unknown"
        for entry in raw.get("registeredEntries", []):
            if entry.get("register") == "1":
                for d in entry.get("descriptions", []):
                    if d.get("languageCode") == "3":
                        status = d.get("description", "unknown")
                        break
                break

        return CompanyRecord(
            business_id=business_id,
            name=current_name,
            auxiliary_names=aux_names,
            company_form=company_form,
            registration_date=registration_date,
            business_line_code=bl_code,
            business_line_description=bl_desc,
            street_address=street_address,
            post_code=post_code,
            city=city,
            status=status,
        )
