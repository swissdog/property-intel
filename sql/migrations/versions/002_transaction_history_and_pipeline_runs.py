"""Add transaction_history and pipeline_run tables for audit trail.

transaction_history: tracks every change to a transaction record (price, area, etc.)
pipeline_run: logs each pipeline execution with source counts and timing.

Revision ID: 002_history
Revises: 001_initial
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "002_history"
down_revision = "001_initial"
branch_labels = None
depends_on = None

SCHEMA = "property"


def upgrade() -> None:
    # Transaction change history
    op.create_table(
        "transaction_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("transaction_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("source_record_id", sa.String(200), nullable=False),
        sa.Column("field", sa.String(50), nullable=False),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("run_id", sa.String(36), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_txn_history_source_record",
        "transaction_history",
        ["source_record_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_txn_history_changed_at",
        "transaction_history",
        ["changed_at"],
        schema=SCHEMA,
    )

    # Pipeline execution log
    op.create_table(
        "pipeline_run",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("sources_json", sa.Text, nullable=True),
        sa.Column("records_fetched", sa.Integer, server_default="0"),
        sa.Column("records_written", sa.Integer, server_default="0"),
        sa.Column("records_changed", sa.Integer, server_default="0"),
        sa.Column("problems_json", sa.Text, nullable=True),
        sa.Column("elapsed_seconds", sa.Float, nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_pipeline_run_started",
        "pipeline_run",
        ["started_at"],
        schema=SCHEMA,
    )

    # Add first_seen_at to transaction (to track when we first saw a record)
    op.add_column(
        "transaction",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # Backfill first_seen_at from fetched_at for existing rows
    op.execute(f"UPDATE {SCHEMA}.transaction SET first_seen_at = fetched_at WHERE first_seen_at IS NULL")


def downgrade() -> None:
    op.drop_column("transaction", "first_seen_at", schema=SCHEMA)
    op.drop_table("pipeline_run", schema=SCHEMA)
    op.drop_table("transaction_history", schema=SCHEMA)
