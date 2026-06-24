#!/bin/bash
# Runs every backend service natively on the host (no Docker, no image
# rebuilds) against the Dockerized Postgres/Redis from `docker compose
# up`. This is the fast-iteration path — uvicorn --reload picks up code
# changes immediately. Postgres and Redis stay containerized because
# they're infrastructure you're not editing; the services are code you
# are, so they run directly.
#
# Prereq: `docker compose up -d` already running (postgres + redis).
#
# Usage: scripts/dev.sh [command]
#   startall        Start every service + worker (default if no command given)
#   killall         Stop every service + worker this script started
#   kill <name>     Stop one service or worker by name (see `list` for names)
#   list            Show which services/workers are currently running
#   help            Show this message
#
# `startall` still blocks and traps Ctrl+C exactly like before (so the
# old interactive workflow keeps working) -- but since Claude Code
# (or anything else starting this headlessly/backgrounded) has no TTY to
# Ctrl+C, `killall`/`kill <name>` give an explicit way to stop things
# from a separate invocation, without needing to find/signal the
# original process. `list` is read-only, safe to run anytime.
set -e

cd "$(dirname "$0")/.."

PID_FILE="logs/dev.pids"

# Services bound to a fixed port -- killed/listed via pattern-matching on
# that port, not a tracked PID. uvicorn --reload spawns a parent watcher
# process plus a child server process; `$!` at launch only ever captures
# the parent, and killing just the parent doesn't reliably take the child
# down too. Matching on the full command line (which always includes the
# port) catches both, and survives across separate script invocations
# without needing any state file for these.
#
# Plain function + case, not `declare -A` -- macOS's system /bin/bash is
# 3.2 (GPLv2 licensing freeze), which has no associative arrays at all
# (bash 4+ only); this needs to run with whatever bash ships on the dev
# machine, not a Homebrew-installed newer one.
SERVICE_NAMES="catalog theatre booking payment user local-cdn-mock routing agent"

service_port() {
  case "$1" in
    catalog) echo 8001 ;;
    theatre) echo 8002 ;;
    booking) echo 8003 ;;
    payment) echo 8004 ;;
    user) echo 8005 ;;
    local-cdn-mock) echo 8006 ;;
    routing) echo 8000 ;;
    agent) echo 8007 ;;
    *) return 1 ;;
  esac
}

# Workers (sweep/relay/sync replicas) have no port to key off, and run
# via plain `python -m` (no reload indirection) -- multiple replicas of
# the *same* worker share an identical command line, so pattern-matching
# can't tell individual replicas apart by name. These are tracked by
# literal PID in $PID_FILE, written at start time, for *that* precision
# (e.g. `kill reconciliation-sweep-1` specifically, not its sibling).
#
# But the PID file is not the only source of truth for "is this worker
# alive" -- it's empty/missing whenever nothing has called `startall` in
# this invocation's lineage, and `killall` deletes it entirely once it's
# done. `list`/`kill <name>` must still correctly report a worker dead-
# or-alive even then (e.g. a replica that crashed, or was started by a
# since-ended session) -- worker_module's pattern match is the fallback
# for exactly that case, just without per-replica precision.
WORKER_NAMES="reconciliation-sweep-1 reconciliation-sweep-2 theatre-outbox-relay-1 theatre-outbox-relay-2 theatre-availability-sync"

worker_module() {
  case "$1" in
    reconciliation-sweep-1|reconciliation-sweep-2) echo "adapters.reconciliation_sweep" ;;
    theatre-outbox-relay-1|theatre-outbox-relay-2) echo "adapters.theatre_outbox_relay" ;;
    theatre-availability-sync) echo "adapters.theatre_availability_sync" ;;
    *) return 1 ;;
  esac
}

# PID this worker name is recorded against in $PID_FILE, only if that PID
# is still actually alive (a stale/dead entry is treated as not found).
worker_pid_from_file() {
  local name="$1" pid
  [ -f "$PID_FILE" ] || return 1
  pid=$(grep "^$name " "$PID_FILE" 2>/dev/null | awk '{print $2}')
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  echo "$pid"
}

usage() {
  sed -n '11,17p' "$0" | sed 's/^# //; s/^#//'
}

service_pattern() {
  echo "uvicorn main:app --host 0.0.0.0 --port $1"
}

cmd_list() {
  printf "%-26s %-9s %s\n" "NAME" "STATUS" "DETAIL"
  for name in $SERVICE_NAMES; do
    port="$(service_port "$name")"
    pid=$(pgrep -f "$(service_pattern "$port")" | head -1 || true)
    if [ -n "$pid" ]; then
      printf "%-26s %-9s :%s (pid %s)\n" "$name" "running" "$port" "$pid"
    else
      printf "%-26s %-9s :%s\n" "$name" "stopped" "$port"
    fi
  done

  # Module-level live-PID counts, consumed positionally as each replica
  # name below claims one -- imprecise about *which* replica a given pid
  # actually is when the file can't say, but correctly reports how many
  # of each worker are alive either way.
  sweep_live=$(pgrep -f "$(worker_module reconciliation-sweep-1)" | wc -l | tr -d ' ')
  relay_live=$(pgrep -f "$(worker_module theatre-outbox-relay-1)" | wc -l | tr -d ' ')
  sync_live=$(pgrep -f "$(worker_module theatre-availability-sync)" | wc -l | tr -d ' ')

  for wname in $WORKER_NAMES; do
    pid="$(worker_pid_from_file "$wname" || true)"
    if [ -n "$pid" ]; then
      printf "%-26s %-9s pid %s\n" "$wname" "running" "$pid"
      continue
    fi
    case "$wname" in
      reconciliation-sweep-*) live_count="$sweep_live" ;;
      theatre-outbox-relay-*) live_count="$relay_live" ;;
      theatre-availability-sync) live_count="$sync_live" ;;
    esac
    if [ "$live_count" -gt 0 ]; then
      printf "%-26s %-9s running, pid untracked (no live entry in %s)\n" "$wname" "running" "$PID_FILE"
      case "$wname" in
        reconciliation-sweep-*) sweep_live=$((sweep_live - 1)) ;;
        theatre-outbox-relay-*) relay_live=$((relay_live - 1)) ;;
        theatre-availability-sync) sync_live=$((sync_live - 1)) ;;
      esac
    else
      printf "%-26s %-9s\n" "$wname" "stopped"
    fi
  done
}

cmd_kill_one() {
  local name="$1"
  local port
  if port="$(service_port "$name")"; then
    local pids
    pids=$(pgrep -f "$(service_pattern "$port")" || true)
    if [ -z "$pids" ]; then
      echo "$name is not running."
      return 0
    fi
    echo "Stopping $name (pid(s): $pids)..."
    kill $pids
    return 0
  fi

  local module
  if module="$(worker_module "$name")"; then
    local pid
    pid="$(worker_pid_from_file "$name" || true)"
    if [ -n "$pid" ]; then
      echo "Stopping $name (pid $pid)..."
      kill "$pid"
      grep -v "^$name " "$PID_FILE" > "$PID_FILE.tmp" 2>/dev/null || true
      mv "$PID_FILE.tmp" "$PID_FILE" 2>/dev/null || true
      return 0
    fi
    # Not in the PID file (missing/stale entry) -- fall back to killing
    # one live process matching this worker's module, same imprecision
    # as `list`'s fallback: correct that *a* replica died, not which.
    pid=$(pgrep -f "$module" | head -1 || true)
    if [ -z "$pid" ]; then
      echo "$name is not running."
      return 0
    fi
    echo "Stopping $name (pid $pid, untracked -- no live entry in $PID_FILE)..."
    kill "$pid"
    return 0
  fi

  echo "Unknown service/worker name: $name" >&2
  echo "Run 'scripts/dev.sh list' to see known names." >&2
  return 1
}

cmd_killall() {
  echo "Stopping all services..."
  for name in $SERVICE_NAMES; do
    pids=$(pgrep -f "$(service_pattern "$(service_port "$name")")" || true)
    if [ -n "$pids" ]; then
      echo "  $name (pid(s): $pids)"
      kill $pids 2>/dev/null || true
    fi
  done

  # Workers: kill by module pattern, not just whatever's in $PID_FILE --
  # the file won't have an entry for a replica started by a since-ended
  # session, or one that's drifted stale, and killall must still actually
  # stop it (same gap as list/kill_one's fallback, for the same reason).
  echo "Stopping all workers..."
  for module in adapters.reconciliation_sweep adapters.theatre_outbox_relay adapters.theatre_availability_sync; do
    pids=$(pgrep -f "$module" || true)
    if [ -n "$pids" ]; then
      echo "  $module (pid(s): $pids)"
      kill $pids 2>/dev/null || true
    fi
  done
  rm -f "$PID_FILE"
  echo "Done."
}

cmd_startall() {
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
  for svc in services/catalog services/theatre services/booking services/payment services/user local-cdn-mock routing services/agent-service; do
    pip install -q -r "$svc/requirements.txt"
  done

  mkdir -p logs
  : > "$PID_FILE"  # fresh file -- this run's workers are the only ones tracked
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

  start_worker () {
    # $module is a dotted module path (e.g. adapters.reconciliation_sweep),
    # run via `python -m` rather than a file path -- `python path/to/x.py`
    # sets sys.path[0] to that file's own directory, breaking any worker
    # that imports sibling modules from the same package (adapters.X,
    # domain.X); `python -m pkg.module` sets sys.path[0] to cwd instead,
    # which resolves correctly (found while wiring up the Phase 9.5
    # outbox relay and availability sync workers, the first workers here
    # with cross-module imports of their own -- reconciliation_sweep.py
    # never needed this since it has none).
    local name="$1" dir="$2" module="$3"
    shift 3
    echo "Starting $name (log: logs/$name.log)"
    (cd "$dir" && env "$@" PYTHONPATH="$REPO_ROOT" python -m "$module") \
      > "logs/$name.log" 2>&1 &
    local pid=$!
    PIDS+=("$pid")
    echo "$name $pid" >> "$PID_FILE"
  }

  PG="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_HOST_PORT}"
  REDIS="redis://localhost:${REDIS_HOST_PORT}"

  echo "Applying migrations..."
  python infra/migrations/run_migrations.py catalog "$PG/catalog_db"
  python infra/migrations/run_migrations.py theatre "$PG/theatre_db"
  python infra/migrations/run_migrations.py asset "$PG/asset_db"
  python infra/migrations/run_migrations.py booking "$PG/booking_db"
  python infra/migrations/run_migrations.py payment "$PG/payment_db"
  python infra/migrations/run_migrations.py user "$PG/user_db"

  start_service catalog services/catalog 8001 \
    AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/catalog_db"

  start_service theatre services/theatre 8002 \
    AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/theatre_db" \
    BOOKING_SERVICE_URL="http://localhost:8003" CATALOG_SERVICE_URL="http://localhost:8001"

  start_service booking services/booking 8003 \
    AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/booking_db" \
    REDIS_URL="$REDIS" PAYMENT_SERVICE_URL="http://localhost:8004" \
    THEATRE_MOCK_HOLD_MODE="${THEATRE_MOCK_HOLD_MODE:-success}" \
    THEATRE_MOCK_CONFIRM_HOLD_FAILS="${THEATRE_MOCK_CONFIRM_HOLD_FAILS:-false}"

  start_service payment services/payment 8004 \
    AUTH_ENABLED="$AUTH_ENABLED" DATABASE_URL="$PG/payment_db"

  # §5.4: N replicas, exactly one ever active via the Postgres advisory
  # lock -- 2 here is enough to demonstrate the failover property locally.
  start_worker reconciliation-sweep-1 services/booking adapters.reconciliation_sweep \
    DATABASE_URL="$PG/booking_db"
  start_worker reconciliation-sweep-2 services/booking adapters.reconciliation_sweep \
    DATABASE_URL="$PG/booking_db"

  # §5.7/Phase 9.5: same N-replicas-one-active profile as the sweep workers
  # above, for the Outbox relay (confirm_hold/release_hold retries) and the
  # theatre availability sync job (shadow-inventory reconciliation).
  start_worker theatre-outbox-relay-1 services/booking adapters.theatre_outbox_relay \
    DATABASE_URL="$PG/booking_db"
  start_worker theatre-outbox-relay-2 services/booking adapters.theatre_outbox_relay \
    DATABASE_URL="$PG/booking_db"
  start_worker theatre-availability-sync services/booking adapters.theatre_availability_sync \
    DATABASE_URL="$PG/booking_db"

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
    USER_SERVICE_URL="http://localhost:8005" \
    AGENT_SERVICE_URL="http://localhost:8007"

  start_service agent services/agent-service 8007 \
    OLLAMA_URL="http://localhost:11434" \
    OLLAMA_MODEL="llama3.2:3b" \
    BOOKING_PLATFORM_URL="http://localhost:8000" \
    AUTH_ENABLED="$AUTH_ENABLED"

  echo ""
  echo "All services starting. Tail any log with: tail -f logs/<name>.log"
  echo "Health checks: see README.md"
  echo "Press Ctrl+C to stop everything, or from another shell: scripts/dev.sh killall"

  trap 'echo ""; echo "Stopping all services..."; kill "${PIDS[@]}" 2>/dev/null; rm -f "$PID_FILE"; exit 0' INT TERM
  wait
}

case "${1:-startall}" in
  startall) cmd_startall ;;
  killall) cmd_killall ;;
  kill)
    if [ -z "${2:-}" ]; then
      echo "Usage: scripts/dev.sh kill <name>" >&2
      exit 1
    fi
    cmd_kill_one "$2"
    ;;
  list) cmd_list ;;
  help|-h|--help) usage ;;
  *)
    echo "Unknown command: $1" >&2
    usage
    exit 1
    ;;
esac
