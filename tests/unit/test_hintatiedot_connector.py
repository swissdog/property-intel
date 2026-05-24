"""Tests for the hintatiedot.fi connector — column swap detection and validation."""

import pytest

from jarvis_property_intel.connectors.hintatiedot.connector import HintatiedotConnector


@pytest.fixture
def connector():
    return HintatiedotConnector()


class TestNormalizeRow:
    """Test _normalize_row with various column arrangements."""

    def test_normal_row(self, connector):
        """Standard row with correct column order."""
        row = [
            "Munkkiniemi",       # neighborhood
            "3h+k+s",            # room_config
            "kt",                # building_type
            "75",                # living_area_m2
            "350000",            # debt_free_price
            "4667",              # price_per_m2
            "1965",              # year_built
            "3/5",               # floor
            "on",                # elevator
            "hyvä",              # condition
            "oma",               # lot_type
            "C",                 # energy_class
        ]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is not None
        assert rec.data["debt_free_price"] == 350000
        assert rec.data["living_area_m2"] == 75
        # price_per_m2 is recalculated, not from source
        assert abs(rec.data["price_per_m2"] - 4666.7) < 1

    def test_swapped_price_and_area(self, connector):
        """Detects and fixes clearly swapped columns (price < 1000, area > 10000)."""
        row = [
            "Kallio",
            "2h+k",
            "kt",
            "250000",            # area field has price value
            "45",                # price field has area value
            "5556",
            "1920",
            "4/5",
            "on",
            "tyyd.",
            "vuokra",
            "D",
        ]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is not None
        # Should be corrected: price=250000, area=45
        assert rec.data["debt_free_price"] == 250000
        assert rec.data["living_area_m2"] == 45

    def test_price_per_m2_in_price_field(self, connector):
        """Detects price_per_m2 stored in price field, debt_free_price in area field."""
        row = [
            "Keskusta",
            "3h+k+s",
            "kt",
            "363000",            # area field has debt_free_price
            "4125",              # price field has price_per_m2
            "2007",              # original price_per_m2 (year-like)
            "",                  # year_built
            "2/4",
            "on",
            "hyvä",
            "oma",
            "B",
        ]
        rec = connector._normalize_row(row, "Espoo")
        assert rec is not None
        # After correction: price should be ~363000, area should be ~88
        assert rec.data["debt_free_price"] == 363000
        assert 50 < rec.data["living_area_m2"] < 150

    def test_rejects_implausible_price(self, connector):
        """Row with price below 5000€ is rejected."""
        row = ["X", "1h", "kt", "30", "3000", "100", "2000", "", "", "", "", ""]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is None

    def test_rejects_implausible_area(self, connector):
        """Row with area > 1000 m² is rejected."""
        row = ["X", "1h", "kt", "1500", "200000", "133", "2000", "", "", "", "", ""]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is None

    def test_rejects_zero_price(self, connector):
        row = ["X", "1h", "kt", "50", "0", "0", "2000", "", "", "", "", ""]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is None

    def test_stable_record_id(self, connector):
        """Record ID doesn't change when price changes (based on physical characteristics)."""
        row1 = ["Kallio", "2h+k", "kt", "50", "200000", "4000", "1970", "3/5", "on", "hyvä", "oma", "C"]
        row2 = ["Kallio", "2h+k", "kt", "50", "210000", "4200", "1970", "3/5", "on", "hyvä", "oma", "C"]
        rec1 = connector._normalize_row(row1, "Helsinki")
        rec2 = connector._normalize_row(row2, "Helsinki")
        assert rec1 is not None and rec2 is not None
        # Same physical property, different price → same record_id
        assert rec1.source_record_id == rec2.source_record_id

    def test_different_properties_get_different_ids(self, connector):
        """Different physical properties get different record IDs."""
        row1 = ["Kallio", "2h+k", "kt", "50", "200000", "4000", "1970", "3/5", "on", "hyvä", "oma", "C"]
        row2 = ["Kallio", "3h+k+s", "kt", "75", "300000", "4000", "1970", "3/5", "on", "hyvä", "oma", "C"]
        rec1 = connector._normalize_row(row1, "Helsinki")
        rec2 = connector._normalize_row(row2, "Helsinki")
        assert rec1 is not None and rec2 is not None
        assert rec1.source_record_id != rec2.source_record_id

    def test_recalculates_price_per_m2(self, connector):
        """price_per_m2 is always recalculated from validated price/area."""
        row = ["Kallio", "2h+k", "kt", "50", "200000", "9999", "1970", "", "", "", "", ""]
        rec = connector._normalize_row(row, "Helsinki")
        assert rec is not None
        # 200000 / 50 = 4000, not the source value 9999
        assert rec.data["price_per_m2"] == 4000.0
