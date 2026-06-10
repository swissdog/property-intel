"""Add results_json to pipeline_run for per-source run detail.

The hourly pipeline builds a detailed per-source results dict (statfi/paavo/
oikotie/hintatiedot/mml counters, view refreshes, table counts) but until now
only logged it. Persist it on completion so the UI can report the latest
run's per-source breakdown, not just the summed totals.

Revision ID: 020_pipeline_run_results
Revises: 019_transit_stop
Create Date: 2026-06-10
"""

from alembic import op

revision = "020_pipeline_run_results"
down_revision = "019_transit_stop"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.pipeline_run ADD COLUMN IF NOT EXISTS results_json TEXT"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.pipeline_run DROP COLUMN IF EXISTS results_json"
    )
