"""Unit tests for GTFS stop parsing + transit-accessibility scoring (pure)."""

from __future__ import annotations

import pytest

from jarvis_property_intel.connectors.gtfs import parse_stops_txt, transit_access_score

STOPS_TXT = """stop_id,stop_name,stop_lat,stop_lon,location_type
1001,Rautatientori,60.1710,24.9410,0
1002,Kamppi,60.1690,24.9320,
2001,Pasila asema,60.1990,24.9330,1
3001,Bad Coords,0,0,0
4001,Out Of Range,95.0,24.0,0
5001,,60.2000,24.9000,0
,NoId,60.3,24.8,0
"""


def test_parse_stops_keeps_only_boardable_valid():
    stops = parse_stops_txt(STOPS_TXT, feed="hsl")
    ids = {s.stop_id for s in stops}
    # 1001 (location_type 0) and 1002 (empty type) and 5001 (no name but valid) kept
    assert ids == {"1001", "1002", "5001"}
    # station (2001), 0/0 coords (3001), out-of-range (4001), no stop_id are dropped
    assert "2001" not in ids and "3001" not in ids and "4001" not in ids


def test_parse_stops_fields():
    stops = {s.stop_id: s for s in parse_stops_txt(STOPS_TXT, feed="hsl")}
    s = stops["1001"]
    assert s.feed == "hsl"
    assert s.name == "Rautatientori"
    assert s.lat == pytest.approx(60.1710)
    assert s.lon == pytest.approx(24.9410)
    assert stops["5001"].name is None  # blank name normalised to None


def test_parse_stops_empty():
    assert parse_stops_txt("stop_id,stop_name,stop_lat,stop_lon\n", feed="x") == []


def test_score_none_when_no_nearby_stop():
    assert transit_access_score(None, 0, 0) is None


def test_score_doorstep_dense_is_max():
    # nearest 0 m, 12 stops within 800 m → walk=100, density=100 → 100
    assert transit_access_score(0.0, 6, 12) == 100.0


def test_score_far_and_sparse_is_low():
    # nearest exactly 800 m → walk 0; 0 stops → density 0 → 0
    assert transit_access_score(800.0, 0, 0) == 0.0


def test_score_is_monotonic_in_distance():
    near = transit_access_score(100.0, 2, 4)
    far = transit_access_score(600.0, 2, 4)
    assert near > far


def test_score_caps_distance_beyond_800():
    # beyond 800 m the walk component floors at 0; density still contributes
    s = transit_access_score(5000.0, 0, 3)
    assert s == pytest.approx(0.4 * 30.0, abs=0.05)  # 0.6*0 + 0.4*min(100,30)
