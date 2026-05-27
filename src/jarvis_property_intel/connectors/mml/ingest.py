"""Pure mapping from normalized MML records to transaction-table params.

Kept free of any DB/network so it is unit-testable. The hourly pipeline's
``write_mml_to_db`` uses :func:`record_to_transaction_params`, then adds the
matched ``asset_id`` and ingest timestamps before upserting.

MML kauppahintarekisteri carries the REAL deed date (``kauppapvm`` /
``luovutuspvm``), so ``sale_date_precision`` is always ``"exact"`` — unlike the
KVKL/hintatiedot source whose rows are ``"unknown"``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from ..base import NormalizedRecord

#: Source key written to property.transaction.source. Matches DataSource.mml_transactions
#: and the migration-018 backfill rule (source ILIKE '%mml%' → precision 'exact').
MML_SOURCE = "mml_transactions"


def parse_iso_date(value: Any) -> date | None:
    """Parse an ISO date/datetime string (or ``date``) into a ``date``, else None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def record_to_transaction_params(
    record: NormalizedRecord, now: datetime
) -> dict[str, Any] | None:
    """Map one normalized MML transaction record to transaction-table params.

    Returns ``None`` when the record lacks what we require — a usable deed date
    and a positive price. The returned dict omits ``asset_id`` and
    ``first_seen_at``; the writer fills those in after parcel matching.
    """
    if record.record_type != "transaction":
        return None
    d = record.data
    sale_date = parse_iso_date(d.get("transaction_date"))
    price = d.get("transaction_price")
    if sale_date is None or not price or price <= 0:
        return None
    return {
        "transaction_id": str(uuid.uuid4()),
        "source": MML_SOURCE,
        "source_record_id": record.source_record_id,
        # MML has the real deed date: use it for the legacy NOT NULL column and
        # the canonical sale_date, and flag precision exact.
        "transaction_date": sale_date,
        "sale_date": sale_date,
        "sale_date_precision": "exact",
        "transaction_price": float(price),
        "transaction_type": d.get("transaction_type") or "sale",
        "parcel_id": d.get("parcel_id"),
        "municipality": d.get("municipality_name") or d.get("municipality_code") or "",
        "price_per_m2": d.get("unit_price_m2"),
        "fetched_at": now,
    }
