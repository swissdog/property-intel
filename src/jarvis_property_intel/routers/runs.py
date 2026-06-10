"""Pipeline run endpoints — report ingest pipeline run results."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from jarvis_module_sdk import ModuleAuth, verify_module_auth_dependency
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis_property_intel.config import get_settings
from jarvis_property_intel.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/property_intel/runs", tags=["runs"])

_verify_dep = None


def _get_verify():
    global _verify_dep
    if _verify_dep is None:
        s = get_settings()
        _verify_dep = verify_module_auth_dependency(
            secret=s.x_module_auth_secret,
            expected_module="property_intel",
        )
    return _verify_dep


_COLUMNS = """
    run_id, started_at, completed_at, status,
    records_fetched, records_written, records_changed,
    elapsed_seconds, sources_json, problems_json, results_json
"""


def _parse_json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _row_to_run(row: Any) -> dict[str, Any]:
    d = dict(row._mapping)
    d["sources"] = _parse_json(d.pop("sources_json", None)) or []
    d["problems"] = _parse_json(d.pop("problems_json", None)) or []
    # results_json on per-lähde-erittely (vain 020-migraation jälkeiset ajot)
    d["results"] = _parse_json(d.pop("results_json", None))
    return d


@router.get("")
async def list_runs(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(20, ge=1, le=200),
) -> list[dict[str, Any]]:
    """List recent pipeline runs, newest first."""
    sql = text(f"""
        SELECT {_COLUMNS}
        FROM property.pipeline_run
        ORDER BY started_at DESC
        LIMIT :limit
    """)
    result = await session.execute(sql, {"limit": limit})
    return [_row_to_run(row) for row in result.fetchall()]


@router.get("/latest")
async def latest_run(
    auth: Annotated[ModuleAuth, Depends(_get_verify())],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Return the most recently started pipeline run."""
    sql = text(f"""
        SELECT {_COLUMNS}
        FROM property.pipeline_run
        ORDER BY started_at DESC
        LIMIT 1
    """)
    result = await session.execute(sql)
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="no pipeline runs recorded")
    return _row_to_run(row)
