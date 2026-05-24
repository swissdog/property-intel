"""Connector registry — central catalogue of all source connectors.

The registry keeps track of which connectors are available and whether each
one is currently enabled.  Consumers query the registry to discover
connectors by source-id or by protocol type.
"""

from __future__ import annotations

from .base import SourceConnector


class ConnectorRegistry:
    """Registry for source connectors.  Connectors are individually toggleable."""

    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}
        self._enabled: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, connector: SourceConnector, *, enabled: bool = True) -> None:
        """Register a connector, optionally marking it as disabled.

        If a connector with the same ``source_id`` is already registered it
        will be replaced silently.
        """
        self._connectors[connector.source_id] = connector
        self._enabled[connector.source_id] = enabled

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------

    def enable(self, source_id: str) -> None:
        """Enable a previously registered connector.

        Raises ``KeyError`` if *source_id* is not registered.
        """
        if source_id not in self._connectors:
            raise KeyError(f"Unknown connector: {source_id!r}")
        self._enabled[source_id] = True

    def disable(self, source_id: str) -> None:
        """Disable a registered connector.

        Raises ``KeyError`` if *source_id* is not registered.
        """
        if source_id not in self._connectors:
            raise KeyError(f"Unknown connector: {source_id!r}")
        self._enabled[source_id] = False

    def is_enabled(self, source_id: str) -> bool:
        """Return whether *source_id* is registered **and** enabled."""
        return self._enabled.get(source_id, False)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, source_id: str) -> SourceConnector | None:
        """Return the connector for *source_id*, or ``None`` if not found.

        Disabled connectors are still returned; check :meth:`is_enabled`
        separately if needed.
        """
        return self._connectors.get(source_id)

    def get_enabled(self) -> list[SourceConnector]:
        """Return all currently enabled connectors."""
        return [
            conn
            for sid, conn in self._connectors.items()
            if self._enabled.get(sid, False)
        ]

    def get_by_type(self, connector_type: type) -> list[SourceConnector]:
        """Return all **enabled** connectors that are instances of *connector_type*.

        This is useful for narrowing down to e.g. all
        :class:`ListingSourceConnector` implementations::

            listings = registry.get_by_type(ListingSourceConnector)
        """
        return [
            conn
            for conn in self.get_enabled()
            if isinstance(conn, connector_type)
        ]

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._connectors)

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._connectors

    def __repr__(self) -> str:
        enabled = sum(1 for v in self._enabled.values() if v)
        return (
            f"<ConnectorRegistry connectors={len(self._connectors)} "
            f"enabled={enabled}>"
        )
