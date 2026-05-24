#!/bin/bash
# Restore property-intel v1 dump into v2 PG (jarvis_property_intel @ 5435).
#
# Prerequisites:
#   1. jarvis-pg running on 5435 (compose.infra-v2.yml)
#   2. pg-init.sql already executed (jarvis_property_intel DB + property_intel_user exist)
#   3. Dump file at ~/backups/property-intel-dump-2026-05-24.pg
#
# Usage:
#   PGPASSWORD=<property_intel_user_pw> bash scripts/restore-from-v1.sh

set -euo pipefail

DUMP_FILE="${1:-$HOME/backups/property-intel-dump-2026-05-24.pg}"
PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5435}"
PG_USER="${PG_USER:-property_intel_user}"
PG_DB="${PG_DB:-jarvis_property_intel}"

if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR: dump file not found: $DUMP_FILE"
    exit 1
fi

echo "Restoring $DUMP_FILE → $PG_USER@$PG_HOST:$PG_PORT/$PG_DB"
pg_restore \
    -h "$PG_HOST" \
    -p "$PG_PORT" \
    -U "$PG_USER" \
    -d "$PG_DB" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    "$DUMP_FILE"

echo "Restore complete. Verify with: make stats"
