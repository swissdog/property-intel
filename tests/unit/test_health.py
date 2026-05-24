"""Health endpoint test — no auth required."""

import httpx
import pytest
from httpx import ASGITransport

from jarvis_property_intel.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["module"] == "property_intel"
    assert "version" in body
