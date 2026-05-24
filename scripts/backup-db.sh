#!/bin/bash
# Property-intel PostGIS backup → local file + GCS
set -e
TIMESTAMP=$(date +%Y-%m-%d_%H%M)
BACKUP_DIR="/home/sami/code/JARVIS/property-intel/data/db-backups"
mkdir -p "$BACKUP_DIR"

# pg_dump from Docker
docker exec -e PGPASSWORD=property_dev property-db pg_dump -U property -d property_intel --schema=property -F c \
  > "${BACKUP_DIR}/property_intel_${TIMESTAMP}.dump"

# Compress
gzip -f "${BACKUP_DIR}/property_intel_${TIMESTAMP}.dump"

SIZE=$(du -h "${BACKUP_DIR}/property_intel_${TIMESTAMP}.dump.gz" | cut -f1)
echo "Backup created: ${BACKUP_DIR}/property_intel_${TIMESTAMP}.dump.gz (${SIZE})"

# Upload to GCS
if command -v gsutil &> /dev/null; then
  gsutil cp "${BACKUP_DIR}/property_intel_${TIMESTAMP}.dump.gz" \
    "gs://parkkidata-backups/db/${TIMESTAMP}.dump.gz" 2>/dev/null && \
    echo "Uploaded to GCS: gs://parkkidata-backups/db/${TIMESTAMP}.dump.gz" || \
    echo "GCS upload failed (non-critical)"
fi

# Keep only last 7 local backups
ls -t "${BACKUP_DIR}"/property_intel_*.dump.gz 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null
echo "Backup complete"
