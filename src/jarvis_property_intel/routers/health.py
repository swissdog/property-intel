"""Health-check endpoint for the Property Intelligence module."""

from fastapi import APIRouter

from jarvis_property_intel import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "property_intel", "version": __version__}
