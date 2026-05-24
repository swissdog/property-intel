"""Property Intel — Ingest Worker.

Scheduled worker that runs source connectors to fetch raw data into the
bronze layer. Each source runs on its own configured cadence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zlib
from datetime import datetime, timezone
from pathlib import Path

from jarvis_property_intel.connectors.base import RawFetchResult, SourceConnector
from jarvis_property_intel.connectors.registry import ConnectorRegistry
from jarvis_property_intel.connectors.mml import MMLConfig, MMLTransactionConnector
from jarvis_property_intel.connectors.statfi import StatFiConfig, StatFiPxWebConnector
from jarvis_property_intel.connectors.paavo import PaavoConfig, PaavoConnector

logger = logging.getLogger(__name__)

RAW_STORAGE = Path(os.getenv("PROPERTY_RAW_STORAGE_PATH", "./data/property-raw"))


def _build_registry() -> ConnectorRegistry:
    """Build connector registry from environment toggles."""
    registry = ConnectorRegistry()

    if os.getenv("PROPERTY_SOURCE_MML_ENABLED", "true").lower() == "true":
        registry.register(MMLTransactionConnector(MMLConfig()))
    else:
        registry.register(MMLTransactionConnector(MMLConfig()), enabled=False)

    if os.getenv("PROPERTY_SOURCE_STATFI_ENABLED", "true").lower() == "true":
        registry.register(StatFiPxWebConnector(StatFiConfig()))
    else:
        registry.register(StatFiPxWebConnector(StatFiConfig()), enabled=False)

    if os.getenv("PROPERTY_SOURCE_PAAVO_ENABLED", "true").lower() == "true":
        registry.register(PaavoConnector(PaavoConfig()))
    else:
        registry.register(PaavoConnector(PaavoConfig()), enabled=False)

    return registry


def store_raw_snapshot(result: RawFetchResult) -> str:
    """Compress and store a raw fetch result to filesystem. Returns storage path."""
    now = datetime.now(timezone.utc)
    date_dir = now.strftime("%Y-%m-%d")
    ts = now.strftime("%H%M%S")
    record_id = result.source_record_id or "unknown"
    safe_id = record_id.replace("/", "_").replace("\\", "_")[:100]

    ext = ".json.zst" if "json" in result.content_type else ".html.zst"
    rel_path = f"{result.source_id}/{date_dir}/{safe_id}_{ts}{ext}"
    full_path = RAW_STORAGE / rel_path

    full_path.parent.mkdir(parents=True, exist_ok=True)
    compressed = zlib.compress(result.raw_content, level=6)
    full_path.write_bytes(compressed)

    logger.info("Stored raw snapshot: %s (%d -> %d bytes)", rel_path, len(result.raw_content), len(compressed))
    return str(rel_path)


async def run_connector(connector: SourceConnector) -> int:
    """Run a single connector's fetch cycle. Returns count of snapshots stored."""
    source = connector.source_id
    logger.info("Starting ingest for source: %s", source)

    try:
        healthy = await connector.health_check()
        if not healthy:
            logger.warning("Source %s health check failed, skipping", source)
            return 0
    except Exception as exc:
        logger.warning("Source %s health check error: %s", source, exc)
        return 0

    try:
        results = await connector.fetch()
    except Exception as exc:
        logger.error("Source %s fetch failed: %s", source, exc)
        return 0

    count = 0
    for result in results:
        try:
            store_raw_snapshot(result)
            count += 1
        except Exception as exc:
            logger.error("Failed to store snapshot from %s: %s", source, exc)

    logger.info("Source %s: stored %d raw snapshots", source, count)
    return count


async def run_all(registry: ConnectorRegistry) -> dict[str, int]:
    """Run all enabled connectors sequentially."""
    results: dict[str, int] = {}
    for connector in registry.get_enabled():
        count = await run_connector(connector)
        results[connector.source_id] = count
    return results


async def main() -> None:
    """Entry point for ingest worker."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Property Intel ingest worker starting")

    registry = _build_registry()
    logger.info("Registry: %d connectors (%d enabled)", len(registry), len(registry.get_enabled()))

    results = await run_all(registry)
    total = sum(results.values())
    logger.info("Ingest complete: %d total snapshots from %d sources", total, len(results))


if __name__ == "__main__":
    asyncio.run(main())
