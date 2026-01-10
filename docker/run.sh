#!/bin/bash
set -e

echo "🚀 Running database migrations..."
/app/.venv/bin/alembic upgrade head
echo "✅ Database migrations completed."

# Give some time for the database to settle
sleep 2

echo "🚀 Starting Application..."
exec /app/.venv/bin/python -m scripts.tasks_cleanup