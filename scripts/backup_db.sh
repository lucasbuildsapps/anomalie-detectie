#!/usr/bin/env bash
# Handmatige/cron-backup van de SENTINEL-database.
#
# SQLite (lokaal):   ./scripts/backup_db.sh
# Postgres:          DATABASE_URL=postgres://... ./scripts/backup_db.sh
#
# In de productie-compose-stack doet de 'backup'-service dit al dagelijks.
set -euo pipefail

STAMP=$(date +%F-%H%M)
DEST="${BACKUP_DIR:-./backups}"
mkdir -p "$DEST"

if [ -n "${DATABASE_URL:-}" ]; then
    pg_dump "$DATABASE_URL" | gzip > "$DEST/sentinel-$STAMP.sql.gz"
    echo "Postgres-backup: $DEST/sentinel-$STAMP.sql.gz"
else
    SRC="${DB_PATH:-./data/store.db}"
    if [ ! -f "$SRC" ]; then
        echo "Geen database gevonden op $SRC" >&2
        exit 1
    fi
    # sqlite3 .backup is consistent, ook bij open verbindingen
    if command -v sqlite3 >/dev/null; then
        sqlite3 "$SRC" ".backup '$DEST/store-$STAMP.db'"
    else
        cp "$SRC" "$DEST/store-$STAMP.db"
    fi
    gzip -f "$DEST/store-$STAMP.db"
    echo "SQLite-backup: $DEST/store-$STAMP.db.gz"
fi

# Retentie: 14 dagen
find "$DEST" -name "*.gz" -mtime +14 -delete
