"""Domain enumerations for property-intel."""

from enum import Enum


class AssetType(str, Enum):
    detached_house = "detached_house"
    apartment_unit = "apartment_unit"
    rowhouse_unit = "rowhouse_unit"
    semi_detached_unit = "semi_detached_unit"


class ListingStatus(str, Enum):
    active = "active"
    removed = "removed"
    sold_unknown = "sold_unknown"
    expired = "expired"


class ListingEventType(str, Enum):
    created = "created"
    price_changed = "price_changed"
    description_changed = "description_changed"
    images_changed = "images_changed"
    removed = "removed"


class TransactionType(str, Enum):
    sale = "sale"
    gift = "gift"
    exchange = "exchange"
    expropriation = "expropriation"
    other = "other"


class SaleDatePrecision(str, Enum):
    """How precise the recorded sale_date is.

    exact   = real per-transaction sale date (e.g. MML kauppapvm).
    quarter = only the sale quarter is known (e.g. KVKL aggregate).
    unknown = no sale date available (do NOT present ingest date as sale date).
    """

    exact = "exact"
    quarter = "quarter"
    unknown = "unknown"


class MatchStatus(str, Enum):
    confirmed = "confirmed"
    rejected = "rejected"
    pending = "pending"


class DataSource(str, Enum):
    oikotie = "oikotie"
    etuovi = "etuovi"
    mml_transactions = "mml_transactions"
    mml_geospatial = "mml_geospatial"
    statfi_pxweb = "statfi_pxweb"
    paavo = "paavo"
    energy_cert = "energy_cert"
    manual_import = "manual_import"
