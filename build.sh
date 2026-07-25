#!/usr/bin/env bash
#
# Compose our patched Endurain from pristine upstream + patches/ + overlay/.
# Distro-patchset model: this repo is the source of truth; build/ is disposable.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REF="$(tr -d '[:space:]' < "$ROOT/upstream.ref")"
BUILD="$ROOT/build"
SRC="$BUILD/source"
APP="$BUILD/app"
FRONTEND="$BUILD/frontend"
VENV="$APP/.venv"

# Where to get pristine upstream. Default: reuse local checkout if present,
# else clone the tag. Override with ENDURAIN_UPSTREAM_DIR / ENDURAIN_REPO_URL.
UPSTREAM_DIR="${ENDURAIN_UPSTREAM_DIR:-/home/brian/dev/endurain}"
REPO_URL="${ENDURAIN_REPO_URL:-https://github.com/endurain-project/endurain}"

command -v git >/dev/null || { echo "ERROR: git not found"; exit 1; }
command -v uv   >/dev/null || { echo "ERROR: uv not found"; exit 1; }
command -v node >/dev/null || { echo "ERROR: node not found"; exit 1; }
command -v npm  >/dev/null || { echo "ERROR: npm not found"; exit 1; }

echo "==> upstream ref : $REF"
echo "==> build output : $BUILD"

# --- 1. Pristine upstream tree -> build/source -------------------------------
echo "==> [1/6] Materialising pristine upstream @ $REF -> $SRC"
rm -rf "$BUILD"
mkdir -p "$SRC"
if [ -d "$UPSTREAM_DIR/.git" ]; then
  # Verify the local checkout is at the pinned ref; export a clean tree via git archive.
  gotref="$(git -C "$UPSTREAM_DIR" describe --tags --exact-match 2>/dev/null || true)"
  if [ "$gotref" != "$REF" ]; then
    echo "    local checkout at '$gotref' != '$REF'; cloning instead"
    git clone --depth 1 --branch "$REF" "$REPO_URL" "$SRC.clone"
    git -C "$SRC.clone" archive HEAD | tar -C "$SRC" -xf -
    rm -rf "$SRC.clone"
  else
    git -C "$UPSTREAM_DIR" archive "$REF" | tar -C "$SRC" -xf -
  fi
else
  git clone --depth 1 --branch "$REF" "$REPO_URL" "$SRC.clone"
  git -C "$SRC.clone" archive HEAD | tar -C "$SRC" -xf -
  rm -rf "$SRC.clone"
fi

# --- 2. Apply patches in series order ---------------------------------------
echo "==> [2/6] Applying patches"
git -C "$SRC" init -q
git -C "$SRC" add -A
git -C "$SRC" -c user.email=b@x -c user.name=b commit -qm pristine
if [ -s "$ROOT/patches/series" ]; then
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    case "$p" in \#*) continue;; esac
    echo "    apply: $p"
    if ! git -C "$SRC" apply --whitespace=nowarn "$ROOT/patches/$p"; then
      echo "ERROR: patch failed to apply: $p" >&2
      echo "       upstream may have moved; refresh this patch against $REF." >&2
      exit 1
    fi
  done < "$ROOT/patches/series"
else
  echo "    (no patches in series)"
fi

# --- 3. Copy overlay (verbatim-new files) -----------------------------------
echo "==> [3/6] Copying overlay"
[ -d "$ROOT/overlay/backend" ]  && cp -a "$ROOT/overlay/backend/."  "$SRC/backend/"
[ -d "$ROOT/overlay/frontend" ] && cp -a "$ROOT/overlay/frontend/." "$SRC/frontend/"

# --- 4. Build frontend ------------------------------------------------------
echo "==> [4/6] Building frontend"
( cd "$SRC/frontend/app" && npm ci && NODE_OPTIONS="--max-old-space-size=2048" npm run build )
mkdir -p "$FRONTEND"
cp -a "$SRC/frontend/app/dist/." "$FRONTEND/"
cat > "$FRONTEND/env.js" <<EOF
window.env = {
  ENDURAIN_HOST: "${ENDURAIN_HOST:-http://localhost:8080}",
};
EOF

# --- 5. Backend app + venv --------------------------------------------------
echo "==> [5/6] Installing backend"
mkdir -p "$APP"
cp -a "$SRC/backend/app/." "$APP/"
find "$APP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
uv venv --python ">=3.13" "$VENV"
REQ="$BUILD/requirements.txt"
( cd "$SRC/backend" && uvx --from poetry --with poetry-plugin-export \
    poetry export -f requirements.txt --without-hashes ) > "$REQ"
uv pip install --python "$VENV/bin/python" --requirement "$REQ"
uv pip install --python "$VENV/bin/python" shapely pyproj

# --- 6. Done ----------------------------------------------------------------
echo "==> [6/6] Build complete."
echo "    app=$APP  frontend=$FRONTEND  venv=$VENV"
echo "Next: cp local/env.local.example local/.env && ./local/setup-db.sh && ./local/run.sh"
