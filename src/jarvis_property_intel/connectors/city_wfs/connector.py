"""Municipal WFS parking zone connector.

Fetches parking zone polygons from Finnish cities' WFS/OGC API services.
All data is CC BY 4.0 licensed. No authentication required.

Sources:
  Helsinki: kartta.hel.fi WFS — payment zones, parking spots, resident zones
  Tampere:  geodata.tampere.fi WFS — parking areas with restrictions
  Turku:    turku.asiointi.fi OGC API — payment zones, machines, resident zones
  Pori:     data-pori.opendata.arcgis.com — parking areas (ArcGIS)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from ..base import NormalizedRecord, RawFetchResult

logger = logging.getLogger(__name__)

# City WFS configurations
CITY_SOURCES = {
    "helsinki": {
        "layers": [
            {
                "name": "Pysakoinnin_maksuvyohykkeet_alue",
                "url": "https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=avoindata:Pysakoinnin_maksuvyohykkeet_alue&outputFormat=application/json&count=5000&srsName=EPSG:4326",
                "zone_type": "payment",
            },
            {
                "name": "Asukas_ja_yrityspysakointivyohykkeet_alue",
                "url": "https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=avoindata:Asukas_ja_yrityspysakointivyohykkeet_alue&outputFormat=application/json&count=5000&srsName=EPSG:4326",
                "zone_type": "resident",
            },
            {
                "name": "Pysakointipaikat_alue",
                "url": "https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=avoindata:Pysakointipaikat_alue&outputFormat=application/json&count=10000&srsName=EPSG:4326",
                "zone_type": "spot",
            },
            {
                "name": "Matkailuliikenne_pysakointipaikat_piste",
                "url": "https://kartta.hel.fi/ws/geoserver/avoindata/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=avoindata:Matkailuliikenne_pysakointipaikat_piste&outputFormat=application/json&count=5000&srsName=EPSG:4326",
                "zone_type": "tourism",
            },
        ],
        "crs": "EPSG:4326",
    },
    "tampere": {
        "layers": [
            {
                "name": "pysakointi_pysakointipaikat_polygon_gk24",
                "url": "https://geodata.tampere.fi/geoserver/ows?service=wfs&version=2.0.0&request=GetFeature&typeName=liikennealueet:pysakointi_pysakointipaikat_polygon_gk24&outputFormat=application/json&count=5000&srsName=EPSG:4326",
                "zone_type": "mixed",
            },
        ],
        "crs": "EPSG:4326",
    },
    "turku": {
        "layers": [
            {
                "name": "Pysakoinnin_maksuvyohykkeet",
                "url": "https://turku.asiointi.fi/trimbleogcapi/collections/GIS:Pysakoinnin_maksuvyohykkeet/items?f=json&limit=500",
                "zone_type": "payment",
            },
            {
                "name": "Asukaspysakointialueet",
                "url": "https://turku.asiointi.fi/trimbleogcapi/collections/GIS:Asukaspysakointialueet/items?f=json&limit=500",
                "zone_type": "resident",
            },
        ],
        "crs": "EPSG:3067",
    },
    "espoo": {
        "layers": [
            {
                "name": "PKSEspooPysakointialueet",
                "url": "https://kartat.espoo.fi/TeklaOgcWeb/WFS.ashx?service=WFS&version=1.0.0&request=GetFeature&typeName=GIS:PKSEspooPysakointialueet&maxFeatures=10000&srsName=EPSG:4326",
                "zone_type": "mixed",
                "format": "gml",
            },
        ],
        "crs": "EPSG:4326",
    },
    "kuopio": {
        "layers": [
            {
                "name": "infrao_liikennemerkki_parking",
                "url": "https://ws.kuopio.fi/wfs?service=WFS&version=2.0.0&request=GetFeature&typeName=avoin:infrao_liikennemerkki_ogc&outputFormat=application/json&count=5000&CQL_FILTER=cid_liikennemerkkityyppi%20LIKE%20%27%25Pys%C3%A4k%C3%B6intipaikka%25%27",
                "zone_type": "sign",
            },
        ],
        "crs": "EPSG:3067",
    },
    "kouvola": {
        "layers": [
            {
                "name": "Liikennemerkki_parking",
                "url": "https://kouvola.asiointi.fi/trimbleogcapi/collections/infrao:Liikennemerkki/items?limit=10000",
                "zone_type": "sign",
                "filter_parking_signs": True,
            },
        ],
        "crs": "EPSG:4326",
    },
    "hameenlinna": {
        "layers": [
            {
                "name": "Liikennemerkki_parking",
                "url": "https://kartta.hameenlinna.fi/trimbleogcapi/collections/infrao:Liikennemerkki/items?limit=10000",
                "zone_type": "sign",
                "filter_parking_signs": True,
            },
        ],
        "crs": "EPSG:4326",
    },
}


class CityWfsConnector:
    """Fetches parking zone polygons from municipal WFS/OGC APIs."""

    source_id = "city_wfs"

    def __init__(self, cities: list[str] | None = None, timeout: float = 60.0) -> None:
        self._cities = cities or list(CITY_SOURCES.keys())
        self._timeout = timeout

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Check first configured city's first layer
                first_city = self._cities[0]
                cfg = CITY_SOURCES.get(first_city, CITY_SOURCES.get("helsinki"))
                if not cfg:
                    return False
                url = cfg["layers"][0]["url"]
                # Don't add count= if already present
                if "count=" not in url and "maxFeatures=" not in url:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}count=1"
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False

    async def fetch(self, **kwargs: Any) -> list[RawFetchResult]:
        """Fetch all configured WFS layers."""
        now = datetime.now(UTC)
        results: list[RawFetchResult] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for city in self._cities:
                cfg = CITY_SOURCES.get(city)
                if not cfg:
                    continue

                for layer in cfg["layers"]:
                    try:
                        resp = await client.get(layer["url"])
                        resp.raise_for_status()
                        results.append(
                            RawFetchResult(
                                source_id=self.source_id,
                                fetched_at=now,
                                raw_content=resp.content,
                                content_type="application/json",
                                parse_version=f"wfs_{city}_{layer['zone_type']}",
                                url=layer["url"],
                                source_record_id=f"{city}_{layer['name']}",
                            )
                        )
                        logger.info("[%s] Fetched %s: %d bytes", city, layer["name"], len(resp.content))
                    except Exception as e:
                        logger.error("[%s] Failed to fetch %s: %s", city, layer["name"], e)

        return results

    def normalize(self, raw: RawFetchResult) -> list[NormalizedRecord]:
        """Parse GeoJSON or GML features into normalized zone records."""
        city = raw.source_record_id.split("_")[0] if raw.source_record_id else "unknown"
        zone_type = raw.parse_version.rsplit("_", 1)[-1] if raw.parse_version else "unknown"

        content = raw.raw_content
        # Detect GML/XML vs GeoJSON
        if content[:5] in (b"<?xml", b"\xef\xbb\xbf<?"):
            return self._normalize_gml(content, city, zone_type, raw.fetched_at)

        data = json.loads(content)
        features = data.get("features") or []

        # Parking sign keywords for filtering Trimble Liikennemerkki data
        _PARKING_SIGN_KEYWORDS = ("pysäköinti", "pysakointi", "parking")

        records: list[NormalizedRecord] = []
        for i, feat in enumerate(features):
            props = feat.get("properties") or {}
            geom = feat.get("geometry")

            # For sign layers: filter to only parking-related signs
            if zone_type == "sign":
                sign_type = (
                    props.get("liikennemerkkityyppi2020") or
                    props.get("liikennemerkkityyppi") or
                    props.get("cid_liikennemerkkityyppi") or ""
                )
                sign_lower = str(sign_type).lower()
                if not any(kw in sign_lower for kw in _PARKING_SIGN_KEYWORDS):
                    # Also check numeric codes: 521 = parking place
                    if not sign_lower.startswith("521"):
                        continue

            # Build zone ID
            fid = feat.get("id") or props.get("id") or str(i)
            zone_id = f"{city}_{fid}"

            # Extract common fields (varies by city — Tekla, GeoServer, Trimble all differ)
            zone_name = (
                props.get("nimi") or props.get("nimi_fi") or
                props.get("tunnus") or props.get("name") or
                props.get("name_fi") or props.get("kohteen_tyyppi") or
                props.get("class") or  # Espoo Tekla
                props.get("cid_liikennemerkkityyppi") or  # Kuopio signs
                ""
            )

            capacity = (
                props.get("paikat_ala") or props.get("paikkamaara") or
                props.get("parking_spaces") or  # Espoo Tekla
                None
            )

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="parking_zone",
                    source_record_id=zone_id,
                    data={
                        "city": city.capitalize(),
                        "zone_name": zone_name,
                        "zone_type": zone_type,
                        "geometry": json.dumps(geom) if geom else None,
                        "capacity": capacity,
                        "restriction_type": props.get("rajoitustyyppi"),
                        "max_duration_hours": _parse_duration(props.get("suurin_sallittu_pysakointiaika") or props.get("kesto")),
                        "resident_zone_id": props.get("asukaspysakointitunnus") or props.get("asukas_yrityspysakointialue"),
                        "valid_hours": props.get("voimassaolo") or props.get("rajoitus_maksullinen_arkena"),
                        "payment_zone": props.get("maksuvyohyke"),
                        "properties_raw": props,
                    },
                    fetched_at=raw.fetched_at,
                )
            )

            # Also emit a facility record with centroid for kohteet table
            centroid = _geojson_centroid(geom) if geom else None
            if centroid:
                records.append(
                    NormalizedRecord(
                        source_id=self.source_id,
                        record_type="parking_facility",
                        source_record_id=zone_id,
                        data={
                            "name": zone_name,
                            "lat": centroid[1],
                            "lng": centroid[0],
                            "city": city.capitalize(),
                            "facility_type": zone_type,
                            "capacity": capacity,
                            "indoor": False,
                            "has_ev_charging": False,
                            "has_anpr": False,
                            "operator": "",
                            "services": [],
                        },
                        fetched_at=raw.fetched_at,
                    )
                )

        logger.info("[%s] Normalized %d records (%s)", city, len(records), zone_type)
        return records


    def _normalize_gml(self, content: bytes, city: str, zone_type: str, fetched_at) -> list[NormalizedRecord]:
        """Parse Tekla WFS GML/XML response (Espoo, Oulu, etc.)."""
        import xml.etree.ElementTree as ET

        records: list[NormalizedRecord] = []
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            logger.error("[%s] GML parse error: %s", city, e)
            return records

        # Find all featureMember elements
        ns = {"gml": "http://www.opengis.net/gml", "wfs": "http://www.opengis.net/wfs"}
        members = root.findall(".//gml:featureMember", ns)

        for i, member in enumerate(members):
            # The first child of featureMember is the feature element
            feat_el = list(member)[0] if len(member) > 0 else None
            if feat_el is None:
                continue

            # Extract properties from child elements (namespace-agnostic)
            props = {}
            lat, lon = None, None
            for child in feat_el:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                # Check for Geometry/Point element
                point = child.find(".//{http://www.opengis.net/gml}coordinates")
                if point is not None and point.text:
                    try:
                        parts = point.text.strip().split(",")
                        lon, lat = float(parts[0]), float(parts[1])
                    except (ValueError, IndexError):
                        pass
                elif child.text:
                    props[tag] = child.text.strip()

            fid = props.get("ID") or props.get("id") or str(i)
            zone_id = f"{city}_{fid}"

            zone_name = (
                props.get("name_fi") or props.get("name") or
                props.get("class") or ""
            )

            capacity = None
            if props.get("parking_spaces"):
                try:
                    capacity = int(props["parking_spaces"])
                except (ValueError, TypeError):
                    pass

            # Build geometry GeoJSON for storage
            geom_json = None
            if lat is not None and lon is not None:
                geom_json = json.dumps({"type": "Point", "coordinates": [lon, lat]})

            records.append(
                NormalizedRecord(
                    source_id=self.source_id,
                    record_type="parking_zone",
                    source_record_id=zone_id,
                    data={
                        "city": city.capitalize(),
                        "zone_name": zone_name,
                        "zone_type": zone_type,
                        "geometry": geom_json,
                        "capacity": capacity,
                        "restriction_type": props.get("class"),
                        "max_duration_hours": None,
                        "resident_zone_id": None,
                        "valid_hours": None,
                        "payment_zone": None,
                        "properties_raw": props,
                    },
                    fetched_at=fetched_at,
                )
            )

            # Also emit facility record for kohteet table (GML has WGS84 coords)
            if lat is not None and lon is not None:
                records.append(
                    NormalizedRecord(
                        source_id=self.source_id,
                        record_type="parking_facility",
                        source_record_id=zone_id,
                        data={
                            "name": zone_name,
                            "lat": lat,
                            "lng": lon,
                            "city": city.capitalize(),
                            "facility_type": zone_type,
                            "capacity": capacity,
                            "indoor": False,
                            "has_ev_charging": False,
                            "has_anpr": False,
                            "operator": "",
                            "services": [],
                        },
                        fetched_at=fetched_at,
                    )
                )

        logger.info("[%s] Normalized %d records from GML (%s)", city, len(records), zone_type)
        return records


def _geojson_centroid(geom: dict | None) -> tuple[float, float] | None:
    """Compute a rough centroid [lon, lat] from a GeoJSON geometry.

    Only returns a result if coordinates are in WGS84 range.
    """
    if not geom:
        return None
    coords = geom.get("coordinates", [])
    gtype = geom.get("type", "")

    # Flatten all coordinate pairs
    flat: list[tuple[float, float]] = []

    def _extract(c):
        if not c:
            return
        if isinstance(c[0], (int, float)):
            flat.append((float(c[0]), float(c[1])))
        else:
            for item in c:
                _extract(item)

    _extract(coords)
    if not flat:
        return None

    avg_lon = sum(p[0] for p in flat) / len(flat)
    avg_lat = sum(p[1] for p in flat) / len(flat)

    # Sanity check: must be WGS84
    if abs(avg_lon) > 180 or abs(avg_lat) > 90:
        return None

    return (avg_lon, avg_lat)


def _parse_duration(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
