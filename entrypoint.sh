#!/bin/sh
set -e

# Wait for the database when one is configured over the network.
if [ -n "$SQLALCHEMY_DATABASE_URI" ] && echo "$SQLALCHEMY_DATABASE_URI" | grep -q "postgresql"; then
  echo "Waiting for database..."
  python - <<'PY'
import os, time, sys
from sqlalchemy import create_engine, text
url = os.environ["SQLALCHEMY_DATABASE_URI"]
for attempt in range(30):
    try:
        create_engine(url).connect().execute(text("SELECT 1"))
        print("Database ready.")
        sys.exit(0)
    except Exception as exc:
        print(f"  not ready ({attempt+1}/30): {exc.__class__.__name__}")
        time.sleep(2)
sys.exit("Database never became available.")
PY
fi

# Prefer Alembic migrations; fall back to create_all on a fresh project.
if [ -d migrations ]; then
  flask db upgrade
else
  echo "No migrations/ directory — creating tables directly."
  flask init-db
fi

exec "$@"
