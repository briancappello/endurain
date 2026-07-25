#!/usr/bin/env bash
#
# Run the locally-built, patched Endurain backend (uvicorn) on bare metal.
# The built SPA is served by the backend from FRONTEND_DIR.
#
# Usage:
#   ./local/run.sh            # serve on http://localhost:8080
#   PORT=9000 ./local/run.sh
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../endurain-fork/local
ROOT="$(cd "$HERE/.." && pwd)"                          # .../endurain-fork

BUILD="$ROOT/build"
APP="$BUILD/app"
FRONTEND="$BUILD/frontend"
VENV="$APP/.venv"
ENV_FILE="$HERE/.env"

[ -x "$VENV/bin/uvicorn" ] || { echo "ERROR: build not found. Run ./build.sh first."; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ERROR: $ENV_FILE not found. Copy local/env.local.example -> local/.env first."; exit 1; }

# Load .env
set -a; . "$ENV_FILE"; set +a

# Point the app at the local build output (config.py reads these env vars).
export BACKEND_DIR="$APP"
export FRONTEND_DIR="$FRONTEND"
export DATA_DIR="${DATA_DIR:-$BUILD/data}"
export LOGS_DIR="${LOGS_DIR:-$BUILD/logs}"
mkdir -p "$DATA_DIR" "$LOGS_DIR"

PORT="${PORT:-8080}"

echo "==> Endurain (local, patched) on http://localhost:$PORT"
echo "    app=$APP"
echo "    frontend=$FRONTEND"
echo "    upstream_sync=${ENDURAIN_UPSTREAM_SYNC:-false}"

cd "$APP"
exec "$VENV/bin/uvicorn" main:app \
  --host 0.0.0.0 --port "$PORT" \
  --log-level "${LOG_LEVEL:-info}"
