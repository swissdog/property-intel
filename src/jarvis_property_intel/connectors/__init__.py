"""property-intel source connector abstraction layer.

Re-exports the core protocols, dataclasses, and the connector registry so
that downstream code can simply::

    from jarvis_property_intel.connectors import (
        SourceConnector,
        ListingSourceConnector,
        TransactionSourceConnector,
        StatisticsSourceConnector,
        RawFetchResult,
        NormalizedRecord,
        ConnectorRegistry,
    )
"""

from .base import (
    ListingSourceConnector,
    NormalizedRecord,
    RawFetchResult,
    SourceConnector,
    StatisticsSourceConnector,
    TransactionSourceConnector,
)
from .registry import ConnectorRegistry

__all__ = [
    "ConnectorRegistry",
    "ListingSourceConnector",
    "NormalizedRecord",
    "RawFetchResult",
    "SourceConnector",
    "StatisticsSourceConnector",
    "TransactionSourceConnector",
]
