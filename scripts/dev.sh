#!/bin/bash
# Runs every backend service natively on the host (no Docker, no image
# rebuilds) against the Dockerized Postgres/Redis from `docker compose
# up`. This is the fast-iteration path — uvicorn --reload picks up code
# changes immediately. Postgres and Redis stay containerized because
# they're infrastructure you're not editing; the services are code you
# are, so they run directly.
#
# Prereq: `docker compose up -d` already running (postgres + redis).
set -e

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found — copy .env.example to .env first." >&2
  exit 1
fi
set -a
source .env
set +a

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating shared virtualenv at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "Installing/updating dependencies for all services..."
for svc in services/catalog services/theatre services/booking services/payment services/user local-cdn-mock routing; do
  pip install -q -r "$svc/requirements.txt"
done

mkdir -p logs
PIDS=()
REPO_ROOT="$(pwd)"

start_service () {
  local name="$1" dir="$2" port="$3"
  shift 3
  echo "Starting $name on :$port (log: logs/$name.log)"
  (cd "$dir" && env "$@" PYTHONPATH="$REPO_ROOT" uvicorn main:app --host 0.0.0.0 --port "$port" --reload) \
    > "logs/$name.log" 2>&1 &
  PIDS+=($!)
}

PG="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_HOST_PORT}"
REDIS="redis://localhost:${REDIS_HOST_PORT}"

echo "Applying migrations..."
python infra/migrations/run_migrations.py catalog "$PG/catalog_db"
python infra/migrations/run_migrations.py theatre "$PG/theatre_db"
python infra/migrations/run_migrations.py asset "$PG/asset_db"
python infra/migrations/run_migrations.py booking "$PG/booking_db"

start_service catalog services/catalog 8001 \
  AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/catalog_db"

start_service theatre services/theatre 8002 \
  AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/theatre_db" \
  BOOKING_SERVICE_URL="http://localhost:8003"

start_service booking services/booking 8003 \
  AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/booking_db" \
  REDIS_URL="$REDIS"

start_service payment services/payment 8004 \
  AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/payment_db"

start_service user services/user 8005 \
  AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/user_db"

start_service local-cdn-mock local-cdn-mock 8006 \
  DATABASE_URL="$PG/asset_db"

start_service routing routing 8000 \
  AUTH_ENABLED="$AUTH_ENABLED" \
  CATALOG_SERVICE_URL="http://localhost:8001" \
  THEATRE_SERVICE_URL="http://localhost:8002" \
  BOOKING_SERVICE_URL="http://localhost:8003" \
  PAYMENT_SERVICE_URL="http://localhost:8004" \
  USER_SERVICE_URL="http://localhost:8005"

echo ""
echo "All services starting. Tail any log with: tail -f logs/<name>.log"
echo "Health checks: see README.md"
echo "Press Ctrl+C to stop everything."

trap 'echo ""; echo "Stopping all services..."; kill "${PIDS[@]}" 2>/dev/null; exit 0' INT TERM
wait
