"""Unit tests for MML connector normalization + transaction-param mapping.

Pure-functional: no network, no DB. Verifies that MML kauppahintarekisteri
records carry the REAL deed date and are flagged sale_date_precision='exact'
(unlike KVKL/hintatiedot which is 'unknown').
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from jarvis_property_intel.connectors.base import NormalizedRecord, RawFetchResult
from jarvis_property_intel.connectors.mml import MMLConfig, MMLTransactionConnector
from jarvis_property_intel.connectors.mml.ingest import (
    MML_SOURCE,
    parse_iso_date,
    record_to_transaction_params,
)

NOW = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)


def _txn_record(**data) -> NormalizedRecord:
    return NormalizedRecord(
        source_id="mml_transactions",
        record_type="transaction",
        source_record_id=data.pop("source_record_id", "mml-1"),
        data=data,
    )


# ---------------------------------------------------------------------------
# parse_iso_date
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2024-11-15", date(2024, 11, 15)),
    ("2024-11-15T08:30:00Z", date(2024, 11, 15)),
    (date(2023, 1, 2), date(2023, 1, 2)),
    (datetime(2023, 1, 2, 5, 0), date(2023, 1, 2)),
    (None, None),
    ("", None),
    ("not-a-date", None),
])
def test_parse_iso_date(value, expected):
    assert parse_iso_date(value) == expected


# ---------------------------------------------------------------------------
# record_to_transaction_params
# ---------------------------------------------------------------------------

def test_params_exact_precision_and_source():
    rec = _txn_record(
        source_record_id="091-123",
        transaction_date="2024-11-15",
        transaction_price=250000,
        parcel_id="091-415-0001-0023",
        municipality_name="Helsinki",
        unit_price_m2=3100.0,
    )
    p = record_to_transaction_params(rec, NOW)
    assert p is not None
    assert p["source"] == MML_SOURCE == "mml_transactions"
    assert p["sale_date"] == date(2024, 11, 15)
    assert p["sale_date_precision"] == "exact"
    # legacy transaction_date mirrors the real deed date for MML (not the ingest day)
    assert p["transaction_date"] == date(2024, 11, 15)
    assert p["transaction_price"] == 250000.0
    assert p["parcel_id"] == "091-415-0001-0023"
    assert p["municipality"] == "Helsinki"
    assert p["source_record_id"] == "091-123"


def test_params_falls_back_to_municipality_code():
    rec = _txn_record(transaction_date="2024-01-01", transaction_price=1,
                       municipality_code="091")
    p = record_to_transaction_params(rec, NOW)
    assert p["municipality"] == "091"


@pytest.mark.parametrize("bad", [
    {"transaction_price": 100000},                       # no date
    {"transaction_date": "2024-11-15"},                  # no price
    {"transaction_date": "2024-11-15", "transaction_price": 0},     # zero price
    {"transaction_date": "2024-11-15", "transaction_price": -5},    # negative price
    {"transaction_date": "bad", "transaction_price": 100000},       # unparseable date
])
def test_params_returns_none_when_unusable(bad):
    assert record_to_transaction_params(_txn_record(**bad), NOW) is None


def test_params_ignores_non_transaction_records():
    rec = NormalizedRecord(
        source_id="mml_transactions", record_type="area_stats",
        source_record_id="x", data={"transaction_date": "2024-11-15",
                                    "transaction_price": 100000},
    )
    assert record_to_transaction_params(rec, NOW) is None


# ---------------------------------------------------------------------------
# Connector OGC normalization → kauppapvm extraction
# ---------------------------------------------------------------------------

def _ogc_raw(features: list[dict]) -> RawFetchResult:
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    return RawFetchResult(
        source_id="mml_transactions",
        fetched_at=NOW,
        raw_content=body,
        content_type="application/geo+json",
        parse_version="ogc_v1",
        url="https://example.test/ogcapi/.../items",
    )


def test_connector_normalize_ogc_to_params_end_to_end():
    connector = MMLTransactionConnector(MMLConfig())
    raw = _ogc_raw([
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [24.94, 60.17]},
            "properties": {
                "tunniste": "kauppa-42",
                "kauppapvm": "2024-06-10",
                "kauppahinta": 615000,
                "kiinteistotunnus": "091-415-0002-0099",
                "kuntanimi": "Helsinki",
                "kuntanumero": "091",
                "yksikkohinta": 4200,
            },
        }
    ])
    records = connector.normalize(raw)
    assert len(records) == 1
    rec = records[0]
    assert rec.record_type == "transaction"
    assert rec.data["transaction_date"] == "2024-06-10"

    p = record_to_transaction_params(rec, NOW)
    assert p["sale_date"] == date(2024, 6, 10)
    assert p["sale_date_precision"] == "exact"
    assert p["parcel_id"] == "091-415-0002-0099"
    assert p["transaction_price"] == 615000.0
