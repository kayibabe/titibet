#!/bin/sh
set -e

# If the database exists but is malformed (e.g. interrupted SFTP upload), remove it
# so SQLAlchemy recreates it cleanly on startup.
if [ -f /data/titibet.db ]; then
    python -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('/data/titibet.db')
    result = conn.execute('PRAGMA integrity_check').fetchone()
    conn.close()
    if result[0] != 'ok':
        sys.exit(1)
except Exception:
    sys.exit(1)
" || {
        echo "WARNING: /data/titibet.db is malformed — removing so the app can start fresh"
        rm /data/titibet.db
    }
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
