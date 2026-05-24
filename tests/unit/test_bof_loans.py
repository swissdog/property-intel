"""Tests for the BoF housing-loan fetcher's pure helpers.

The fetcher (scripts/fetch_bof_loans.py) has two functional helpers worth
covering: _period_to_date (ECB SDW format → datetime.date) and the
LOAN_SERIES manifest (catches accidental duplication or unit drift).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_bof_loans.py"


@pytest.fixture(scope="module")
def fetcher():
    spec = importlib.util.spec_from_file_location("fetch_bof_loans", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_bof_loans"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _period_to_date
# ---------------------------------------------------------------------------

class TestPeriodToDate:

    @pytest.mark.parametrize("raw,expected", [
        ("2024-01", date(2024, 1, 1)),
        ("2020-12", date(2020, 12, 1)),
        ("2026-05", date(2026, 5, 1)),
        # Daily ISO format (some series may be reported daily)
        ("2024-01-15", date(2024, 1, 15)),
        # Whitespace tolerated
        (" 2024-03 ", date(2024, 3, 1)),
    ])
    def test_valid(self, fetcher, raw, expected):
        assert fetcher._period_to_date(raw) == expected

    @pytest.mark.parametrize("raw", [
        "",
        "2024",
        "2024Q1",
        "Jan-2024",
        "garbage",
        "2024-13",   # invalid month
        "2024-00",   # invalid month
    ])
    def test_invalid(self, fetcher, raw):
        assert fetcher._period_to_date(raw) is None


# ---------------------------------------------------------------------------
# LOAN_SERIES manifest sanity
# ---------------------------------------------------------------------------

class TestLoanSeriesManifest:

    def test_metric_codes_unique(self, fetcher):
        codes = [m for m, _u, _d, _s in fetcher.LOAN_SERIES]
        assert len(codes) == len(set(codes)), f"Duplicate metric_code: {codes}"

    def test_units_are_known(self, fetcher):
        valid_units = {"pct", "meur", "eur", "count", "ratio"}
        for metric, unit, _df, _sk in fetcher.LOAN_SERIES:
            assert unit in valid_units, f"{metric}: unknown unit {unit!r}"

    def test_dataflows_are_ecb_known(self, fetcher):
        valid_dataflows = {"MIR", "BSI", "FM", "ICP"}  # core ECB dataflows
        for metric, _u, dataflow, _sk in fetcher.LOAN_SERIES:
            assert dataflow in valid_dataflows, f"{metric}: unknown dataflow {dataflow!r}"

    def test_at_least_one_rate_and_one_volume(self, fetcher):
        units = [u for _m, u, _d, _s in fetcher.LOAN_SERIES]
        assert "pct" in units, "LOAN_SERIES missing any rate (pct) metric"
        assert "meur" in units, "LOAN_SERIES missing any volume (meur) metric"

    def test_finland_country_code_in_series_keys(self, fetcher):
        # ECB SDW series for Finland reporters must include 'FI' as a dimension
        for metric, _u, _df, series_key in fetcher.LOAN_SERIES:
            assert ".FI." in f".{series_key}.", (
                f"{metric}: series_key {series_key!r} missing FI country dimension"
            )
