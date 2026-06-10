"""Property Intelligence module — FastAPI app + v2 lifespan."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from jarvis_module_sdk import ModuleClient, load_manifest

from . import __version__
from .config import get_settings
from .db import dispose, init_engine


def _configure_logging() -> None:
    level_name = os.environ.get("JARVIS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get(
        "JARVIS_LOG_FORMAT",
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.basicConfig(level=level, format=fmt, force=True)


_configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()

    init_engine(s.database_url)
    logger.info(
        "property_intel: db engine ready (%s)", s.database_url.split("@")[-1]
    )

    s.raw_storage_path.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(s.manifest_path)
    logger.info(
        "property_intel: loaded manifest %s v%s", manifest.name, manifest.version
    )

    if s.skip_registration:
        logger.warning(
            "property_intel: skip_registration=True — running without core"
        )
        try:
            yield
        finally:
            await dispose()
        return

    client = ModuleClient(
        core_url=s.core_url,
        bootstrap_token=s.bootstrap_token,
        manifest=manifest,
    )
    async with client:
        logger.info(
            "property_intel: registered with core %s (module_id=%s)",
            s.core_url,
            client.registration.module_id if client.registration else "?",
        )
        try:
            yield
        finally:
            await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="jarvis-property-intel",
        version=__version__,
        description="JARVIS v2 property intelligence module",
        lifespan=lifespan,
    )
    from .routers import (
        analytics,
        areas,
        health,
        intelligence,
        properties,
        runs,
        transactions,
    )

    app.include_router(health.router)
    app.include_router(properties.router)
    app.include_router(areas.router)
    app.include_router(transactions.router)
    app.include_router(analytics.router)
    app.include_router(intelligence.router)
    app.include_router(runs.router)
    return app


app = create_app()
