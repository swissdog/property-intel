#!/bin/bash
# BigQuery backup → GCS (parkkidata-backups bucket)
set -e
PROJECT=parkkidata
DATASET=parkkidatat
BUCKET=gs://parkkidata-backups/bq-backups
TIMESTAMP=$(date +%Y-%m-%d)
BACKUP_DIR="/home/sami/code/JARVIS/property-intel/data/bq-backups"
mkdir -p "$BACKUP_DIR"

echo "Starting BQ backup for $PROJECT:$DATASET → $BUCKET/$TIMESTAMP/"

TABLES=$(bq ls "$PROJECT:$DATASET" 2>/dev/null | grep TABLE | awk '{print $1}')

MANIFEST='{"backup_date":"'$TIMESTAMP'","backed_up_at":"'$(date -u +%Y-%m-%dT%H:%M:%S+00:00)'","project":"'$PROJECT'","dataset":"'$DATASET'","tables":{'
FIRST=true

for TABLE in $TABLES; do
    echo -n "  $TABLE: "
    ROWS=$(bq show --format=json "$PROJECT:$DATASET.$TABLE" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('numRows','0'))")

    bq extract --destination_format=NEWLINE_DELIMITED_JSON --compression=GZIP \
        "$PROJECT:$DATASET.$TABLE" \
        "$BUCKET/$TIMESTAMP/${TABLE}.jsonl.gz" 2>/dev/null

    if [ $? -eq 0 ]; then
        echo "$ROWS rows exported"
        # Also keep local copy
        gsutil cp "$BUCKET/$TIMESTAMP/${TABLE}.jsonl.gz" "$BACKUP_DIR/${TABLE}.jsonl.gz" 2>/dev/null

        if [ "$FIRST" = true ]; then FIRST=false; else MANIFEST="$MANIFEST,"; fi
        MANIFEST="$MANIFEST\"$TABLE\":{\"exported\":true,\"rows\":$ROWS}"
    else
        echo "FAILED"
        if [ "$FIRST" = true ]; then FIRST=false; else MANIFEST="$MANIFEST,"; fi
        MANIFEST="$MANIFEST\"$TABLE\":{\"exported\":false,\"rows\":0}"
    fi
done

MANIFEST="$MANIFEST}}"

echo "$MANIFEST" | python3 -m json.tool > "$BACKUP_DIR/manifest.json"
echo ""
echo "Manifest written to $BACKUP_DIR/manifest.json"
echo "Backup complete"
