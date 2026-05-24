"""Unit tests for SykeFloodConnector parser + scenario routing.

Focus is on pure-functional behavior — REST network calls are not exercised.
We use synthesized RawFetchResult payloads to verify the GeoJSON →
NormalizedRecord transformation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jarvis_property_intel.connectors.base import RawFetchResult
from jarvis_property_intel.connectors.syke_flood import (
    SykeFloodConfig,
    SykeFloodConnector,
    SykeFloodLayer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def connector():
    return SykeFloodConnector(SykeFloodConfig())


def _raw(scenario: str, features: list[dict]) -> RawFetchResult:
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    return RawFetchResult(
        source_id="syke_flood",
        fetched_at=datetime.now(tz=UTC),
        raw_content=body,
        content_type="application/json",
        parse_version=f"esri_geojson_v1:{scenario}",
        url="https://example.test/wfs",
        source_record_id=scenario,
    )


# ---------------------------------------------------------------------------
# Default config sanity
# ---------------------------------------------------------------------------

class TestDefaults:

    def test_default_layers_cover_three_scenarios(self):
        cfg = SykeFloodConfig()
        scenarios = sorted(ly.scenario for ly in cfg.layers)
        assert scenarios == ["100y", "250y", "significant"]

    def test_default_srs_is_wgs84(self):
        assert SykeFloodConfig().out_sr == 4326

    def test_each_layer_has_service_and_layer_id(self):
        for layer in SykeFloodConfig().layers:
            assert isinstance(layer, SykeFloodLayer)
            assert layer.service and "/" not in layer.service
            assert isinstance(layer.layer_id, int) and layer.layer_id >= 0
            assert layer.description


# ---------------------------------------------------------------------------
# normalize() — GeoJSON → NormalizedRecord
# ---------------------------------------------------------------------------

class TestNormalize:

    def test_polygons_become_records_with_scenario_tag(self, connector):
        feature = {
            "id": "fid.42",
            "properties": {"inspireId": "FI.SYKE.NZ.42"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[24.9, 60.1], [24.91, 60.1],
                                  [24.91, 60.11], [24.9, 60.1]]]],
            },
        }
        records = connector.normalize(_raw("100y", [feature]))
        assert len(records) == 1
        rec = records[0]
        assert rec.record_type == "area_stats"
        assert rec.source_id == "syke_flood"
        assert rec.data["scenario"] == "100y"
        assert rec.source_record_id.startswith("100y:")
        assert "geometry" in rec.data
        assert rec.data["properties"]["inspireId"] == "FI.SYKE.NZ.42"

    def test_features_without_geometry_are_kept_but_no_geom_in_data(self, connector):
        # Bare feature with only properties (rare but the parser must tolerate)
        feature = {"id": "fid.no-geom", "properties": {"k": "v"}}
        records = connector.normalize(_raw("250y", [feature]))
        assert len(records) == 1
        assert "geometry" not in records[0].data

    def test_empty_feature_collection_returns_empty_list(self, connector):
        assert connector.normalize(_raw("100y", [])) == []

    def test_unparseable_payload_returns_empty(self, connector):
        bad = RawFetchResult(
            source_id="syke_flood",
            fetched_at=datetime.now(tz=UTC),
            raw_content=b"not json",
            content_type="application/json",
            parse_version="esri_geojson_v1:100y",
            url="https://example.test/wfs",
            source_record_id="100y",
        )
        assert connector.normalize(bad) == []

    def test_scenario_falls_back_to_parse_version_when_record_id_missing(self, connector):
        feature = {"id": "fid.1", "properties": {}, "geometry": None}
        body = json.dumps({"features": [feature]}).encode()
        raw = RawFetchResult(
            source_id="syke_flood",
            fetched_at=datetime.now(tz=UTC),
            raw_content=body,
            content_type="application/json",
            parse_version="esri_geojson_v1:significant",
            url="https://example.test/wfs",
            source_record_id=None,  # forced fallback
        )
        records = connector.normalize(raw)
        assert records[0].data["scenario"] == "significant"

    def test_feature_id_resolution_priority(self, connector):
        # priority: feature.id > properties.OBJECTID > properties.inspireId > uuid
        cases = [
            ({"id": "X", "properties": {"OBJECTID": "Y", "inspireId": "Z"}}, "100y:X"),
            ({"properties": {"OBJECTID": "Y", "inspireId": "Z"}}, "100y:Y"),
            ({"properties": {"inspireId": "Z"}}, "100y:Z"),
        ]
        for feat, expected_prefix in cases:
            feat = {**feat, "geometry": {"type": "Point", "coordinates": [25, 60]}}
            recs = connector.normalize(_raw("100y", [feat]))
            assert recs[0].source_record_id == expected_prefix


# ---------------------------------------------------------------------------
# _scenario_from_parse_version helper
# ---------------------------------------------------------------------------

class TestScenarioExtraction:

    @pytest.mark.parametrize("parse_version,expected", [
        ("esri_geojson_v1:100y", "100y"),
        ("esri_geojson_v1:250y", "250y"),
        ("esri_geojson_v1:significant", "significant"),
        # Edge: malformed tag falls back to "unknown"
        ("plain_no_colon", "unknown"),
    ])
    def test_extraction(self, parse_version, expected):
        assert SykeFloodConnector._scenario_from_parse_version(parse_version) == expected
