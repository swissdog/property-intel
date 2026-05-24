"""Tests for the Oikotie detail-record parser and helpers added in 2026-05-10.

Focus: _parse_energy_class regex correctness, _detail_to_record robustness
against type-variant input from the wild (string fees, int heating codes,
None-or-missing fields).
"""

from datetime import datetime, timezone

import pytest

from jarvis_property_intel.connectors.oikotie.config import OikotieConfig
from jarvis_property_intel.connectors.oikotie.connector import OikotieConnector


@pytest.fixture
def connector():
    return OikotieConnector(OikotieConfig())


# ---------------------------------------------------------------------------
# _parse_energy_class
# ---------------------------------------------------------------------------

class TestParseEnergyClass:

    @pytest.mark.parametrize("raw,expected", [
        # Common Oikotie format: full label
        ("Energialuokka: C2018, Energiatodistuksen voimassaoloaika: 16.12.2034", "C2018"),
        ("Energialuokka: A2013", "A2013"),
        ("Energialuokka: B2018",   "B2018"),
        # Letter-only (no year suffix)
        ("Energialuokka: B", "B"),
        ("Energialuokka: F", "F"),
        # Already-clean code
        ("D2018", "D2018"),
        ("A", "A"),
        # Lowercase
        ("energialuokka c", "C"),
        # Empty / None / wrong format
        (None, None),
        ("", None),
        ("Ei energiatodistusta", None),
        ("Energiatodistuksen voimassa", None),
    ])
    def test_parse(self, raw, expected):
        assert OikotieConnector._parse_energy_class(raw) == expected

    def test_does_not_match_first_e_in_energialuokka(self):
        """Earlier regex bug: matched the 'E' in 'Energialuokka' for C2018 input."""
        result = OikotieConnector._parse_energy_class(
            "Energialuokka: C2018, Energiatodistuksen voimassa"
        )
        assert result == "C2018"  # not 'E'


# ---------------------------------------------------------------------------
# _detail_to_record: full payload with realistic-looking Oikotie response
# ---------------------------------------------------------------------------

class TestDetailToRecord:

    @pytest.fixture
    def sample_card(self):
        """Minimal-but-realistic /api/card/{id} payload."""
        return {
            "cardId": 24483643,
            "url": "https://asunnot.oikotie.fi/myytavat-asunnot/helsinki/24483643",
            "published": "2026-05-09T12:34:56.000Z",
            "coordinates": {"latitude": 60.1998, "longitude": 24.8829},
            "address": {
                "formattedAddress": "Huopalahdentie 14, 00330 Helsinki",
                "zipCode": {"name": "00330"},
                "city": {"name": "Helsinki"},
                "districts": [{"name": "Munkkiniemi"}],
            },
            "building": {
                "buildingType": 1,
                "address": "Huopalahdentie 14",
                "city": "Helsinki",
            },
            "priceData": {
                "price": 283000,
                "shareOfLiabilities": None,
            },
            "adData": {
                "size": 45,
                "rooms": 2,
                "buildingOverrideBuildYear": 1960,
                "buildingOverrideFloors": 7,
                "floor": 5,
                "maintenanceFee": 292.5,
                "managementCharge": 292.5,
                "financialFee": None,
                "waterFee": None,
                "parkingFee": None,
                "saunaCharge": 20,
                "apartmentCondition": 2,
                "heatingInfo": "Kaukolämpö",
                "heatingMethods": ["6"],
                "buildingOverrideBuildingMaterialInfo": "Betoni",
                "buildingOverrideLift": True,
                "buildingOverrideSauna": True,
                "buildingOverrideLotOwnership": 1,
                "buildingOverrideEnergyClass":
                    "Energialuokka: C2018, Energiatodistuksen voimassaoloaika: 16.12.2034",
                "buildingOverrideLotSize": 1249,
            },
        }

    def test_extracts_pricing(self, connector, sample_card):
        rec = connector._detail_to_record(sample_card, datetime.now(timezone.utc))
        assert rec is not None
        d = rec.data
        # Debt-free = price + shareOfLiabilities (which is None ⇒ 0)
        assert d["debt_free_price"] == 283000
        assert d["share_of_liabilities_eur"] is None
        assert d["asking_price"] == 283000

    def test_extracts_recurring_fees(self, connector, sample_card):
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["maintenance_fee_eur"] == 292.5
        assert d["financial_fee_eur"] is None
        assert d["water_fee_eur"] is None
        assert d["sauna_fee_eur"] == 20

    def test_extracts_apartment_attrs(self, connector, sample_card):
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["apartment_condition_code"] == 2
        assert d["heating_method"] == "Kaukolämpö"
        assert d["heating_method_code"] == "6"   # str even though source list[str]
        assert d["building_material"] == "Betoni"
        assert d["has_lift"] is True
        assert d["has_sauna"] is True
        assert d["lot_ownership_code"] == 1
        assert d["energy_class_full"] == "C2018"

    def test_handles_string_fees(self, connector, sample_card):
        """Some listings ship fees as strings like '6.00 e / kk' — must coerce."""
        sample_card["adData"]["parkingFee"] = "6.00 e / kk"
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["parking_fee_eur"] == 6.0

    def test_handles_int_heating_method(self, connector, sample_card):
        """heatingMethods sometimes ships as [int] not [str] — must coerce to str."""
        sample_card["adData"]["heatingMethods"] = [8]   # int, not '8'
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["heating_method_code"] == "8"
        assert isinstance(d["heating_method_code"], str)

    def test_handles_long_heating_text(self, connector, sample_card):
        """Some heatingInfo values exceed 80 chars — must truncate to fit VARCHAR(80)."""
        long_text = "Maalämpö, lattialämmitys + ilmanvaihdon lämmöntalteenotto "  \
                    "+ varaava takka olohuoneessa ja saunassa"
        sample_card["adData"]["heatingInfo"] = long_text
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["heating_method"] is not None
        assert len(d["heating_method"]) <= 80

    def test_handles_missing_optional_fields(self, connector, sample_card):
        """Strip out optional fields — record still produces."""
        for k in ("buildingOverrideLift", "buildingOverrideSauna",
                  "buildingOverrideLotOwnership", "apartmentCondition"):
            sample_card["adData"].pop(k, None)
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["has_lift"] is None
        assert d["has_sauna"] is None
        assert d["lot_ownership_code"] is None
        assert d["apartment_condition_code"] is None

    def test_empty_card_id_returns_none(self, connector):
        assert connector._detail_to_record({}, datetime.now(timezone.utc)) is None

    def test_debt_free_with_liabilities(self, connector, sample_card):
        """When shareOfLiabilities is present, debt_free_price = price + share."""
        sample_card["priceData"]["shareOfLiabilities"] = 50000
        d = connector._detail_to_record(sample_card, datetime.now(timezone.utc)).data
        assert d["debt_free_price"] == 333000
        assert d["share_of_liabilities_eur"] == 50000
