"""Tests for the Paavo full-attribute fetcher's coercion + layer-year logic.

The fetcher script lives at scripts/fetch_paavo_attributes.py. Its key
helpers are pure-functional and worth covering:

* _coerce_number    — handles int/float/str/None and the StatFi -1
                      ("suppressed cell") sentinel
* _layer_year       — extracts the year suffix from a Paavo WFS layer
                      name (postialue:pno_tilasto_2024 → 2024)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load the fetcher module without executing __main__ side effects
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_paavo_attributes.py"


@pytest.fixture(scope="module")
def fetcher():
    spec = importlib.util.spec_from_file_location("fetch_paavo_attributes", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_paavo_attributes"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# _coerce_number
# ---------------------------------------------------------------------------

class TestCoerceNumber:

    @pytest.mark.parametrize("raw,expected", [
        # Plain numerics
        (0, 0.0),
        (5, 5.0),
        (123.4, 123.4),
        # Strings (Paavo serializes some columns as strings)
        ("0", 0.0),
        ("12345", 12345.0),
        ("3.14", 3.14),
        # StatFi sentinel for suppressed cells (n<5)
        (-1, None),
        (-1.0, None),
        ("-1", None),
        # Missing values
        (None, None),
        ("", None),
        # Garbage
        ("n/a", None),
        ("foo", None),
        ([1, 2], None),
    ])
    def test_coerce(self, fetcher, raw, expected):
        assert fetcher._coerce_number(raw) == expected


# ---------------------------------------------------------------------------
# _layer_year
# ---------------------------------------------------------------------------

class TestLayerYear:

    def test_explicit_override_wins(self, fetcher):
        assert fetcher._layer_year("postialue:pno_tilasto_2024", 2099) == 2099

    @pytest.mark.parametrize("layer,expected", [
        ("postialue:pno_tilasto_2024", 2024),
        ("postialue:pno_tilasto_2020", 2020),
        ("pno_tilasto_2018", 2018),
        # Trailing year wins over earlier digits
        ("foo_2010_bar_2024", 2024),
    ])
    def test_parses_year_from_layer(self, fetcher, layer, expected):
        assert fetcher._layer_year(layer, None) == expected

    def test_missing_year_falls_back_to_current(self, fetcher):
        from datetime import UTC, datetime
        assert fetcher._layer_year("postialue:pno_tilasto", None) == datetime.now(UTC).year


# ---------------------------------------------------------------------------
# Attribute prefix matching (sanity test on the constant)
# ---------------------------------------------------------------------------

class TestAttributePrefixes:

    @pytest.mark.parametrize("key,is_attr", [
        ("he_vakiy", True),
        ("hr_mtu", True),
        ("ko_yl_kork", True),
        ("ra_asunn", True),
        ("te_taly", True),
        ("tp_tyopy", True),
        ("pt_tyott", True),
        ("tr_pi_tul", True),
        # Non-attribute fields
        ("postinumeroalue", False),
        ("nimi", False),
        ("kunta", False),
        ("kuntanimi", False),
        ("vuosi", False),
    ])
    def test_prefix_match(self, fetcher, key, is_attr):
        assert key.startswith(fetcher.ATTRIBUTE_PREFIXES) is is_attr
