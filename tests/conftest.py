"""Shared fixtures for property-intel tests."""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path

import jwt
import pytest

TEST_X_MODULE_AUTH_SECRET = "test-x-module-auth-secret-32bytes-padding-padding"

os.environ.setdefault("JARVIS_PROPERTY_INTEL_SKIP_REGISTRATION", "true")
os.environ.setdefault(
    "JARVIS_PROPERTY_INTEL_X_MODULE_AUTH_SECRET", TEST_X_MODULE_AUTH_SECRET
)

_TEST_DB_PATH = Path(tempfile.gettempdir()) / (
    f"jarvis-property-intel-test-{uuid.uuid4().hex[:8]}.db"
)
os.environ.setdefault(
    "JARVIS_PROPERTY_INTEL_DATABASE_URL",
    f"sqlite+aiosqlite:///{_TEST_DB_PATH}",
)


def mint_x_module_auth(
    *,
    user_id: str = "sami",
    scopes: list[str] | None = None,
    module_target: str = "property_intel",
    exp_delta: int = 30,
    secret: str = TEST_X_MODULE_AUTH_SECRET,
) -> str:
    now = int(time.time())
    payload = {
        "iss": "test",
        "sub": f"user:{user_id}",
        "scopes": scopes or ["property_intel:read"],
        "module_target": module_target,
        "iat": now,
        "exp": now + exp_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def auth_headers():
    token = mint_x_module_auth()
    return {"X-Module-Auth": token}
