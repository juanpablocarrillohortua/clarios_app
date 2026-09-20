#!/usr/bin/env bash
# =============================================================================
# Block until PostgreSQL accepts connections, then optionally exec a command.
#
# Compose healthchecks already gate `depends_on`, but this script covers the
# cases they do not: CI service containers (which have no compose healthcheck),
# one-off `docker compose run` invocations, and running migrations from a host
# shell immediately after `make up`.
#
# Usage:
#   ./scripts/wait_for_db.sh                       # wait, then exit 0
#   ./scripts/wait_for_db.sh alembic upgrade head  # wait, then exec the command
#
# Configuration (environment):
#   POSTGRES_HOST      host to probe            (default: db)
#   POSTGRES_PORT      port to probe            (default: 5432)
#   POSTGRES_USER      role to authenticate as  (default: energy)
#   POSTGRES_DB        database to connect to   (default: energy)
#   WAIT_TIMEOUT       seconds before giving up (default: 90)
#   WAIT_INTERVAL      seconds between attempts (default: 2)
#
# Exit codes:
#   0  database is ready (or the exec'd command's own status)
#   1  timed out
# =============================================================================
set -euo pipefail

HOST="${POSTGRES_HOST:-db}"
PORT="${POSTGRES_PORT:-5432}"
USER_NAME="${POSTGRES_USER:-energy}"
DB_NAME="${POSTGRES_DB:-energy}"
TIMEOUT="${WAIT_TIMEOUT:-90}"
INTERVAL="${WAIT_INTERVAL:-2}"

log() {
    printf '[wait_for_db] %s\n' "$1" >&2
}

# pg_isready is the correct probe when available: it distinguishes "accepting
# connections" from "port is open but the server is still recovering".
probe_with_pg_isready() {
    pg_isready --host="${HOST}" --port="${PORT}" --username="${USER_NAME}" \
        --dbname="${DB_NAME}" --quiet
}

# Fallback for images without the PostgreSQL client tools. Only proves the port
# is open, which is weaker but better than nothing.
probe_with_python() {
    python - "$HOST" "$PORT" <<'PYTHON'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(3)
    sys.exit(0 if sock.connect_ex((host, port)) == 0 else 1)
PYTHON
}

if command -v pg_isready >/dev/null 2>&1; then
    probe() { probe_with_pg_isready; }
    log "probing ${HOST}:${PORT} with pg_isready (timeout ${TIMEOUT}s)"
else
    probe() { probe_with_python; }
    log "pg_isready not found; falling back to a TCP probe of ${HOST}:${PORT}"
fi

elapsed=0
until probe; do
    if [ "${elapsed}" -ge "${TIMEOUT}" ]; then
        log "ERROR: database at ${HOST}:${PORT} did not become ready within ${TIMEOUT}s"
        log "Check that the db service is running: docker compose ps db"
        exit 1
    fi
    sleep "${INTERVAL}"
    elapsed=$((elapsed + INTERVAL))
done

log "database is ready after ${elapsed}s"

# Exec so the command replaces this shell and receives signals directly.
if [ "$#" -gt 0 ]; then
    log "executing: $*"
    exec "$@"
fi
