# Endurain Fork Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Endurain customizations currently embedded in the homelab Ansible role into a standalone `~/dev/endurain-fork` repo that owns the changes as a distro-style patchset (pinned upstream ref + ordered unified-diff patches + verbatim overlay files), is runnable on localhost baremetal, and can be committed to independently and consumed by the homelab.

**Architecture:** Distro-patchset model. The repo stores only *what we change*: an `upstream.ref` pin, a `patches/` series of `git apply`-able unified diffs against pristine upstream files, and an `overlay/` tree of genuinely-new files copied verbatim. A `build.sh` fetches pristine upstream at the pinned ref into a gitignored work dir, applies patches in `series` order, copies the overlay, then builds frontend + backend. Local run tooling (DB setup, uvicorn runner) is thin. The homelab role and any local runner both consume this one source of truth.

**Tech Stack:** Bash (`build.sh` compose), `git apply` (patches), `uv` (Python venv/deps), Node/npm (frontend build), Endurain upstream `v0.17.7` (FastAPI backend + Vue 3 SPA), PostgreSQL 18.

## Global Constraints

- Target project dir: `/home/brian/dev/endurain-fork` (new git repo).
- Pinned upstream ref: `v0.17.7` (tag) of `github.com/endurain-project/endurain`.
- Pristine upstream reference tree for generating diffs: `/home/brian/dev/endurain` (verified pristine at `v0.17.7`; only local change is `.gitignore`). Use **read-only**; never commit to or mutate it.
- Content authority (custom code + edit definitions): `/home/brian/dev/homelab/ansible/roles/endurain/` (latest content May 2026). This is the source to extract FROM.
- Build-approach reference (reusable compose/DB/run logic): `/home/brian/dev/endurain/local/` (do not depend on it at runtime; port ideas, not paths).
- Patches are unified diffs applied via `git apply` in the order listed in `patches/series`. Refresh a patch by regenerating with `git diff` against pristine upstream.
- Overlay = genuinely-new files only (do not exist upstream at v0.17.7). Verified new: entire `custom/` backend package, `trail_matcher.py`, `pg18_compat.py`, and frontend `ActivitySegmentsComponent.vue`, `ActivitySegmentsLapsComponent.vue`, `ActivityLapsTableComponent.vue`, `ActivityElevationSpeedChartComponent.vue`, `customSegmentsService.js`.
- Patches (files that exist upstream and are modified): `backend/app/main.py`, `backend/app/core/scheduler.py`, `frontend/app/src/components/Activities/ActivityMapComponent.vue`, `frontend/app/src/views/ActivityView.vue`.
- The build work dir (`build/`) and local secrets (`.env`) are gitignored; the repo commits only `upstream.ref`, `patches/`, `overlay/`, `build.sh`, local run tooling, and docs.
- `ENDURAIN_UPSTREAM_SYNC` gate semantics preserved exactly: default `false` disables Strava/Garmin polling (both scheduled jobs and startup calls).
- Do NOT modify the homelab repo in this plan (another agent is active there). Repointing the homelab role at this fork is a separate, later plan.
- `pg18_compat.py.j2` in homelab contains no Jinja variables; it is copied verbatim as `pg18_compat.py`.

---

### Task 1: Repo skeleton, upstream pin, and gitignore

**Files:**
- Create: `/home/brian/dev/endurain-fork/upstream.ref`
- Create: `/home/brian/dev/endurain-fork/.gitignore`
- Create: `/home/brian/dev/endurain-fork/patches/series`
- Create: `/home/brian/dev/endurain-fork/README.md`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `upstream.ref` — single-line file containing `v0.17.7`. Read by `build.sh` (Task 8) to know which upstream ref to fetch.
  - `patches/series` — newline-delimited ordered list of patch filenames (relative to `patches/`), applied top-to-bottom by `build.sh`. Starts empty (no patches yet).
  - `.gitignore` — ignores `build/`, `*.env`, `.env`, `__pycache__/`.

- [ ] **Step 1: Initialize the git repo**

Run:
```bash
cd /home/brian/dev/endurain-fork && git init && git branch -m main
```
Expected: `Initialized empty Git repository` (or reinitialized) and branch renamed to `main`.

- [ ] **Step 2: Write `upstream.ref`**

Create `/home/brian/dev/endurain-fork/upstream.ref` with exactly:
```
v0.17.7
```
(single line, trailing newline)

- [ ] **Step 3: Write `.gitignore`**

Create `/home/brian/dev/endurain-fork/.gitignore` with:
```gitignore
# Build work dir: pristine upstream copy + applied patches/overlay (reproducible, not committed)
/build/

# Local secrets
.env
*.env

# Python
__pycache__/
*.pyc
```

- [ ] **Step 4: Create the empty patch series file**

Create `/home/brian/dev/endurain-fork/patches/series` as an empty file (patches are appended in later tasks):
```bash
mkdir -p /home/brian/dev/endurain-fork/patches && : > /home/brian/dev/endurain-fork/patches/series
```

- [ ] **Step 5: Write a minimal README**

Create `/home/brian/dev/endurain-fork/README.md`:
```markdown
# endurain-fork

Standalone patchset for [Endurain](https://github.com/endurain-project/endurain),
distro-style: a pinned upstream ref + ordered unified-diff patches + verbatim
overlay files. This repo is the single source of truth for our Endurain
customizations; the homelab and local baremetal builds both consume it.

## Layout

- `upstream.ref` — pinned upstream tag/SHA (currently `v0.17.7`).
- `patches/` — unified diffs applied via `git apply` in `patches/series` order,
  against pristine upstream files.
- `overlay/` — genuinely-new files copied verbatim on top of upstream.
- `build.sh` — fetch pristine upstream @ `upstream.ref` -> apply patches ->
  copy overlay -> build frontend + backend into `build/`.
- `local/` — thin baremetal run tooling (DB setup, uvicorn runner, env example).

## Quick start (local baremetal)

    ./build.sh
    cp local/env.local.example local/.env   # edit secrets
    ./local/setup-db.sh
    ./local/run.sh                            # http://localhost:8080

## Bumping upstream

Edit `upstream.ref`, run `./build.sh`. If a patch no longer applies, `build.sh`
fails loudly naming the offending patch; refresh it (regenerate the diff against
the new pristine upstream) and re-run.
```

- [ ] **Step 6: Commit**

```bash
cd /home/brian/dev/endurain-fork
git add upstream.ref .gitignore patches/series README.md
git commit -m "chore: scaffold endurain-fork patchset repo (pinned v0.17.7)"
```

---

### Task 2: Overlay tree (genuinely-new files, copied verbatim)

Extract every custom file that does NOT exist upstream at v0.17.7 into `overlay/`, mirroring the destination path *inside the upstream tree* so `build.sh` can copy `overlay/<path>` -> `build/source/<path>` directly. Verified-new files only (see Global Constraints).

**Files:**
- Create dir: `/home/brian/dev/endurain-fork/overlay/backend/app/custom/` (entire package)
- Create: `/home/brian/dev/endurain-fork/overlay/backend/app/trail_matcher.py`
- Create: `/home/brian/dev/endurain-fork/overlay/backend/app/pg18_compat.py`
- Create: `/home/brian/dev/endurain-fork/overlay/frontend/app/src/components/Activities/ActivitySegmentsComponent.vue`
- Create: `/home/brian/dev/endurain-fork/overlay/frontend/app/src/components/Activities/ActivitySegmentsLapsComponent.vue`
- Create: `/home/brian/dev/endurain-fork/overlay/frontend/app/src/components/Activities/ActivityLapsTableComponent.vue`
- Create: `/home/brian/dev/endurain-fork/overlay/frontend/app/src/components/Activities/ActivityElevationSpeedChartComponent.vue`
- Create: `/home/brian/dev/endurain-fork/overlay/frontend/app/src/services/customSegmentsService.js`
- Source (read-only): `/home/brian/dev/homelab/ansible/roles/endurain/files/custom/`, `.../files/trail_matcher.py`, `.../templates/pg18_compat.py.j2`

**Interfaces:**
- Consumes: Task 1 repo skeleton.
- Produces: `overlay/` tree. `build.sh` (Task 8) copies `overlay/backend/...` and `overlay/frontend/...` over the corresponding paths in the upstream copy. The backend `custom/` package is imported as top-level `custom` (it does `from core.database import ...`), so it MUST land at `backend/app/custom/` beside upstream `main.py`. `trail_matcher.py` and `pg18_compat.py` land beside `main.py` too (imported as top-level modules).

- [ ] **Step 1: Copy the backend custom package (excluding pycache)**

Run:
```bash
mkdir -p /home/brian/dev/endurain-fork/overlay/backend/app
cp -a /home/brian/dev/homelab/ansible/roles/endurain/files/custom \
      /home/brian/dev/endurain-fork/overlay/backend/app/custom
find /home/brian/dev/endurain-fork/overlay/backend/app/custom -name '__pycache__' -type d -prune -exec rm -rf {} +
find /home/brian/dev/endurain-fork/overlay/backend/app/custom -name '*.pyc' -delete
```
Expected: `overlay/backend/app/custom/` contains `__init__.py`, `__main__.py`, `api.py`, `cli.py`, `merge.py`, `models.py`, `pipeline.py`, `segments.py`, `alembic.ini`, `migrations/`, `static/` — and NO `__pycache__` dirs and NO `frontend/` subdir (frontend files are relocated in Step 3).

- [ ] **Step 2: Verify custom package contents and remove the frontend subdir from it**

The homelab `custom/` dir also contains a `frontend/` subdir (the Vue files + `patch_activity_view.py`). Those belong in the frontend overlay / patches, not the backend package. Remove it from the backend overlay:
```bash
rm -rf /home/brian/dev/endurain-fork/overlay/backend/app/custom/frontend
```
Run to confirm the backend package is clean:
```bash
ls /home/brian/dev/endurain-fork/overlay/backend/app/custom
```
Expected: `__init__.py __main__.py alembic.ini api.py cli.py merge.py migrations models.py pipeline.py segments.py static` (no `frontend`).

- [ ] **Step 3: Copy trail_matcher.py and pg18_compat.py**

Run:
```bash
cp /home/brian/dev/homelab/ansible/roles/endurain/files/trail_matcher.py \
   /home/brian/dev/endurain-fork/overlay/backend/app/trail_matcher.py
cp /home/brian/dev/homelab/ansible/roles/endurain/templates/pg18_compat.py.j2 \
   /home/brian/dev/endurain-fork/overlay/backend/app/pg18_compat.py
```
Expected: both files exist under `overlay/backend/app/`. `pg18_compat.py` has no Jinja placeholders (verified), so the `.j2` copies verbatim.

- [ ] **Step 4: Verify pg18_compat.py has no Jinja variables**

Run:
```bash
grep -n '{{' /home/brian/dev/endurain-fork/overlay/backend/app/pg18_compat.py || echo "OK: no jinja vars"
```
Expected: `OK: no jinja vars`. (If any `{{ }}` appear, STOP — the file needs templating and this task's assumption is wrong.)

- [ ] **Step 5: Copy the new frontend components + service**

Run:
```bash
mkdir -p /home/brian/dev/endurain-fork/overlay/frontend/app/src/components/Activities
mkdir -p /home/brian/dev/endurain-fork/overlay/frontend/app/src/services
FE=/home/brian/dev/homelab/ansible/roles/endurain/files/custom/frontend
DEST=/home/brian/dev/endurain-fork/overlay/frontend/app/src
cp "$FE/ActivitySegmentsComponent.vue"           "$DEST/components/Activities/"
cp "$FE/ActivitySegmentsLapsComponent.vue"       "$DEST/components/Activities/"
cp "$FE/ActivityLapsTableComponent.vue"          "$DEST/components/Activities/"
cp "$FE/ActivityElevationSpeedChartComponent.vue" "$DEST/components/Activities/"
cp "$FE/customSegmentsService.js"                "$DEST/services/"
```
Expected: 4 `.vue` files + `customSegmentsService.js` copied.

- [ ] **Step 6: Verify none of the overlay frontend files exist upstream (they must be new, not diffs)**

Run:
```bash
UP=/home/brian/dev/endurain/frontend/app/src
for f in components/Activities/ActivitySegmentsComponent.vue \
         components/Activities/ActivitySegmentsLapsComponent.vue \
         components/Activities/ActivityLapsTableComponent.vue \
         components/Activities/ActivityElevationSpeedChartComponent.vue \
         services/customSegmentsService.js; do
  if [ -e "$UP/$f" ]; then echo "COLLISION (should be a patch, not overlay): $f"; else echo "OK new: $f"; fi
done
```
Expected: all five print `OK new:`. If any print `COLLISION`, STOP — that file must move to `patches/` (it's a diff), not overlay.

- [ ] **Step 7: Commit**

```bash
cd /home/brian/dev/endurain-fork
git add overlay
git commit -m "feat: add overlay tree (custom backend package + new frontend components)"
```

---

### Task 3: Patch — backend `main.py` (wire custom layer + upstream-sync gate)

Convert the homelab `lineinfile`/`blockinfile`/`replace` edits on `main.py` (homelab build.yml:334-360, 395-428) into a single real unified diff `patches/0001-main-custom-wiring.patch`, generated against pristine upstream `main.py`.

**Files:**
- Create: `/home/brian/dev/endurain-fork/patches/0001-main-custom-wiring.patch`
- Modify: `/home/brian/dev/endurain-fork/patches/series` (append `0001-main-custom-wiring.patch`)
- Source of edit definitions (read-only): `/home/brian/dev/homelab/ansible/roles/endurain/tasks/build.yml` (lines 334-360, 395-428) and `/home/brian/dev/endurain/local/patch_backend.py` (`patch_main`)
- Pristine input (read-only): `/home/brian/dev/endurain/backend/app/main.py`

**Interfaces:**
- Consumes: Task 1 (series file), pristine upstream at `/home/brian/dev/endurain`.
- Produces: `patches/0001-main-custom-wiring.patch` — a `git apply`-able diff with paths relative to the upstream tree root (i.e. `a/backend/app/main.py` / `b/backend/app/main.py`). `build.sh` applies it with `git apply` from `build/source/`.

The edits this patch must encode (idempotency markers dropped — a diff applies once by construction):
1. Insert `import pg18_compat  # noqa: F401 -- PG 18 compat patch` immediately before the first `import os`.
2. Insert `import custom  # noqa: F401 -- custom fork layer` immediately after the `import pg18_compat` line.
3. Insert `    custom.run_migrations()  # run custom fork migrations` immediately after the line `    command.upgrade(alembic_cfg, "head")`.
4. Insert `    custom.register_api(fastapi_app)  # register custom API endpoints` on the line immediately before `fastapi_app.include_router(api_router)` (matching that line's indentation).
5. After the line containing `core_scheduler.start_scheduler()`, insert the startup upstream-sync guard block (`import os as _os`, `_upstream_sync = ...`, and an `if not _upstream_sync:` log line) at that line's indentation.
6. Wrap each of these startup calls in `if _upstream_sync:` (indent the call under it), at their existing indentation:
   - `strava_utils.refresh_strava_tokens(True)`
   - `await garmin_activity_utils.retrieve_garminconnect_users_activities_for_days...`
   - `await strava_activity_utils.retrieve_strava_users_activities_for_days...`
   - `garmin_health_utils.retrieve_garminconnect_users_health_for_days...`

- [ ] **Step 1: Set up a scratch git tree from pristine upstream main.py**

Run:
```bash
mkdir -p /tmp/opencode/efork-patchgen && cd /tmp/opencode/efork-patchgen
rm -rf work && mkdir -p work/backend/app/core && cd work
git init -q
cp /home/brian/dev/endurain/backend/app/main.py backend/app/main.py
git add backend/app/main.py && git commit -qm pristine
```
Expected: a clean git repo at `/tmp/opencode/efork-patchgen/work` with pristine `backend/app/main.py` committed.

- [ ] **Step 2: Apply the six edits to `backend/app/main.py`**

Edit `/tmp/opencode/efork-patchgen/work/backend/app/main.py` to make exactly the six changes listed in the Interfaces block above. Reference the exact injected strings from `/home/brian/dev/endurain/local/patch_backend.py` (function `patch_main`) — use its literal insert text, but WITHOUT the `if "..." not in src` idempotency guards (a diff needs the raw before/after). Preserve the original file's indentation at each anchor.

Note: For edit 5, use the block form (BEGIN/END comment markers are optional in a real patch; prefer a clean block without `# BEGIN/END` markers since the diff itself is the record).

- [ ] **Step 3: Verify the edits are syntactically valid Python**

Run:
```bash
cd /tmp/opencode/efork-patchgen/work && python3 -c "import ast; ast.parse(open('backend/app/main.py').read()); print('main.py parses OK')"
```
Expected: `main.py parses OK`. If it fails, fix the indentation/edit before generating the diff.

- [ ] **Step 4: Generate the unified diff**

Run:
```bash
cd /tmp/opencode/efork-patchgen/work
git diff > /home/brian/dev/endurain-fork/patches/0001-main-custom-wiring.patch
head -5 /home/brian/dev/endurain-fork/patches/0001-main-custom-wiring.patch
```
Expected: patch file begins with `diff --git a/backend/app/main.py b/backend/app/main.py`.

- [ ] **Step 5: Verify the patch applies cleanly against a fresh pristine copy**

Run:
```bash
cd /tmp/opencode/efork-patchgen
rm -rf verify && mkdir -p verify/backend/app && cd verify
cp /home/brian/dev/endurain/backend/app/main.py backend/app/main.py
git init -q && git add . && git commit -qm pristine
git apply --check /home/brian/dev/endurain-fork/patches/0001-main-custom-wiring.patch && echo "APPLIES CLEAN"
```
Expected: `APPLIES CLEAN`.

- [ ] **Step 6: Append to the series file**

Append the line `0001-main-custom-wiring.patch` to `/home/brian/dev/endurain-fork/patches/series`:
```bash
echo "0001-main-custom-wiring.patch" >> /home/brian/dev/endurain-fork/patches/series
```

- [ ] **Step 7: Commit**

```bash
cd /home/brian/dev/endurain-fork
git add patches/0001-main-custom-wiring.patch patches/series
git commit -m "feat: add main.py custom-wiring patch (0001)"
```

---

### Task 4: Patch — backend `core/scheduler.py` (gate upstream-sync jobs)

Convert the homelab scheduler edits (build.yml:362-393) into `patches/0002-scheduler-upstream-sync-gate.patch`.

**Files:**
- Create: `/home/brian/dev/endurain-fork/patches/0002-scheduler-upstream-sync-gate.patch`
- Modify: `/home/brian/dev/endurain-fork/patches/series`
- Source of edit definitions (read-only): `/home/brian/dev/homelab/ansible/roles/endurain/tasks/build.yml` (lines 362-393), `/home/brian/dev/endurain/local/patch_backend.py` (`patch_scheduler`)
- Pristine input (read-only): `/home/brian/dev/endurain/backend/app/core/scheduler.py`

**Interfaces:**
- Consumes: Task 1, pristine upstream.
- Produces: `patches/0002-scheduler-upstream-sync-gate.patch` (paths `a/backend/app/core/scheduler.py`).

Edits to encode:
1. Immediately after the `def start_scheduler(...):` line, insert (at 4-space body indent): `import os as _os` and `_upstream_sync = _os.getenv("ENDURAIN_UPSTREAM_SYNC", "false").lower() == "true"`.
2. For each of these four `add_scheduler_job(` calls whose first argument is the named function, wrap the `add_scheduler_job(...)` call in `if _upstream_sync:` (indent the whole call one level deeper):
   - `strava_utils.refresh_strava_tokens`
   - `strava_activity_utils.retrieve_strava`
   - `garmin_activity_utils.retrieve_garminconnect_users_activities`
   - `garmin_health_utils.retrieve_garminconnect`

- [ ] **Step 1: Scratch tree from pristine scheduler.py**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf sched && mkdir -p sched/backend/app/core && cd sched
git init -q
cp /home/brian/dev/endurain/backend/app/core/scheduler.py backend/app/core/scheduler.py
git add . && git commit -qm pristine
```
Expected: clean repo with pristine `scheduler.py`.

- [ ] **Step 2: Apply the two edits**

Edit `/tmp/opencode/efork-patchgen/sched/backend/app/core/scheduler.py` per the Interfaces block. Use the literal wrapping from `/home/brian/dev/endurain/local/patch_backend.py` `patch_scheduler` (the `if _upstream_sync:` wrap + deeper indent of the multi-line `add_scheduler_job(...)` call), WITHOUT idempotency guards.

- [ ] **Step 3: Verify valid Python**

Run:
```bash
cd /tmp/opencode/efork-patchgen/sched && python3 -c "import ast; ast.parse(open('backend/app/core/scheduler.py').read()); print('scheduler.py parses OK')"
```
Expected: `scheduler.py parses OK`.

- [ ] **Step 4: Generate the diff**

Run:
```bash
cd /tmp/opencode/efork-patchgen/sched
git diff > /home/brian/dev/endurain-fork/patches/0002-scheduler-upstream-sync-gate.patch
head -5 /home/brian/dev/endurain-fork/patches/0002-scheduler-upstream-sync-gate.patch
```
Expected: begins with `diff --git a/backend/app/core/scheduler.py b/backend/app/core/scheduler.py`.

- [ ] **Step 5: Verify clean apply**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf verify2 && mkdir -p verify2/backend/app/core && cd verify2
cp /home/brian/dev/endurain/backend/app/core/scheduler.py backend/app/core/scheduler.py
git init -q && git add . && git commit -qm pristine
git apply --check /home/brian/dev/endurain-fork/patches/0002-scheduler-upstream-sync-gate.patch && echo "APPLIES CLEAN"
```
Expected: `APPLIES CLEAN`.

- [ ] **Step 6: Append to series + commit**

```bash
echo "0002-scheduler-upstream-sync-gate.patch" >> /home/brian/dev/endurain-fork/patches/series
cd /home/brian/dev/endurain-fork
git add patches/0002-scheduler-upstream-sync-gate.patch patches/series
git commit -m "feat: add scheduler upstream-sync gate patch (0002)"
```

---

### Task 5: Patch — `ActivityMapComponent.vue` (segment highlight + position marker)

Convert the homelab `blockinfile` (build.yml:84-242, insert-before `const initMap =`) into `patches/0003-activity-map-highlight.patch`. The exact injected block is already captured in `/home/brian/dev/endurain/local/patch_map_component.py` (constant `BLOCK`, anchor `const initMap =`).

**Files:**
- Create: `/home/brian/dev/endurain-fork/patches/0003-activity-map-highlight.patch`
- Modify: `/home/brian/dev/endurain-fork/patches/series`
- Source of edit definition (read-only): `/home/brian/dev/endurain/local/patch_map_component.py`
- Pristine input (read-only): `/home/brian/dev/endurain/frontend/app/src/components/Activities/ActivityMapComponent.vue`

**Interfaces:**
- Consumes: Task 1, pristine upstream.
- Produces: `patches/0003-activity-map-highlight.patch` (paths `a/frontend/app/src/components/Activities/ActivityMapComponent.vue`).

Edit to encode: insert the `BLOCK` text (segment highlight helpers, position marker, `getWaypoints`, and `defineExpose({...})`) immediately BEFORE the line containing `const initMap =`, at 2-space indent as in `patch_map_component.py`.

- [ ] **Step 1: Confirm the anchor exists upstream exactly once**

Run:
```bash
grep -c 'const initMap =' /home/brian/dev/endurain/frontend/app/src/components/Activities/ActivityMapComponent.vue
```
Expected: `1`. If not 1, STOP — the anchor is ambiguous and the patch strategy needs revisiting.

- [ ] **Step 2: Scratch tree + apply the injection using the existing script**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf map && mkdir -p map/frontend/app/src/components/Activities && cd map
git init -q
cp /home/brian/dev/endurain/frontend/app/src/components/Activities/ActivityMapComponent.vue \
   frontend/app/src/components/Activities/ActivityMapComponent.vue
git add . && git commit -qm pristine
python3 /home/brian/dev/endurain/local/patch_map_component.py \
   frontend/app/src/components/Activities/ActivityMapComponent.vue
```
Expected: prints `[map] injected segment highlight + position marker + defineExpose`.

Note: The script wraps the block in `// BEGIN/END CUSTOM PATCH` marker comments. That is fine — the markers become part of the committed diff and document provenance. Keep them.

- [ ] **Step 3: Generate the diff**

Run:
```bash
cd /tmp/opencode/efork-patchgen/map
git diff > /home/brian/dev/endurain-fork/patches/0003-activity-map-highlight.patch
head -5 /home/brian/dev/endurain-fork/patches/0003-activity-map-highlight.patch
```
Expected: begins with `diff --git a/frontend/app/src/components/Activities/ActivityMapComponent.vue ...`.

- [ ] **Step 4: Verify clean apply**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf verify3 && mkdir -p verify3/frontend/app/src/components/Activities && cd verify3
cp /home/brian/dev/endurain/frontend/app/src/components/Activities/ActivityMapComponent.vue \
   frontend/app/src/components/Activities/ActivityMapComponent.vue
git init -q && git add . && git commit -qm pristine
git apply --check /home/brian/dev/endurain-fork/patches/0003-activity-map-highlight.patch && echo "APPLIES CLEAN"
```
Expected: `APPLIES CLEAN`.

- [ ] **Step 5: Append to series + commit**

```bash
echo "0003-activity-map-highlight.patch" >> /home/brian/dev/endurain-fork/patches/series
cd /home/brian/dev/endurain-fork
git add patches/0003-activity-map-highlight.patch patches/series
git commit -m "feat: add ActivityMapComponent highlight/marker patch (0003)"
```

---

### Task 6: Patch — `ActivityView.vue` (charts + segments/laps tabs + trail description)

Convert the homelab `patch_activity_view.py` string-surgery (build.yml:244-254 runs it) into a real diff `patches/0004-activity-view.patch`. The authoritative edit logic is `/home/brian/dev/homelab/ansible/roles/endurain/files/custom/frontend/patch_activity_view.py` (7 template + script edits).

**Files:**
- Create: `/home/brian/dev/endurain-fork/patches/0004-activity-view.patch`
- Modify: `/home/brian/dev/endurain-fork/patches/series`
- Source of edit definition (read-only): `/home/brian/dev/homelab/ansible/roles/endurain/files/custom/frontend/patch_activity_view.py`
- Pristine input (read-only): `/home/brian/dev/endurain/frontend/app/src/views/ActivityView.vue`

**Interfaces:**
- Consumes: Task 1, pristine upstream.
- Produces: `patches/0004-activity-view.patch` (paths `a/frontend/app/src/views/ActivityView.vue`).

The homelab script performs 7 edits (add `ref="activityMapRef"`; insert custom chart/trail/segments block before `<!-- gear zone -->`; remove upstream `<!-- graphs -->`..`<!-- back button -->` block; add two component imports; add customSegmentsService import; add custom refs + chart hover handlers; add data-fetch block before `isLoading.value = false`; reset custom refs in the route watch). Run the script itself against pristine to produce the exact result, then diff.

- [ ] **Step 1: Scratch tree from pristine ActivityView.vue**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf view && mkdir -p view/frontend/app/src/views && cd view
git init -q
cp /home/brian/dev/endurain/frontend/app/src/views/ActivityView.vue frontend/app/src/views/ActivityView.vue
git add . && git commit -qm pristine
```

- [ ] **Step 2: Run the upstream patch script to transform the file**

Run:
```bash
cd /tmp/opencode/efork-patchgen/view
python3 /home/brian/dev/homelab/ansible/roles/endurain/files/custom/frontend/patch_activity_view.py \
  frontend/app/src/views/ActivityView.vue
```
Expected: ends with `Patched ActivityView.vue (N changes)` where N is 6 or 7. Review the printed lines — any `WARNING: Could not find ...` means an anchor didn't match upstream v0.17.7. If a WARNING appears, STOP and inspect: the anchor text in the script must be reconciled against the actual upstream file before the patch is trustworthy.

- [ ] **Step 3: Generate the diff**

Run:
```bash
cd /tmp/opencode/efork-patchgen/view
git diff > /home/brian/dev/endurain-fork/patches/0004-activity-view.patch
head -5 /home/brian/dev/endurain-fork/patches/0004-activity-view.patch
```
Expected: begins with `diff --git a/frontend/app/src/views/ActivityView.vue ...`.

- [ ] **Step 4: Verify clean apply**

Run:
```bash
cd /tmp/opencode/efork-patchgen && rm -rf verify4 && mkdir -p verify4/frontend/app/src/views && cd verify4
cp /home/brian/dev/endurain/frontend/app/src/views/ActivityView.vue frontend/app/src/views/ActivityView.vue
git init -q && git add . && git commit -qm pristine
git apply --check /home/brian/dev/endurain-fork/patches/0004-activity-view.patch && echo "APPLIES CLEAN"
```
Expected: `APPLIES CLEAN`.

- [ ] **Step 5: Append to series + commit**

```bash
echo "0004-activity-view.patch" >> /home/brian/dev/endurain-fork/patches/series
cd /home/brian/dev/endurain-fork
git add patches/0004-activity-view.patch patches/series
git commit -m "feat: add ActivityView charts/segments patch (0004)"
```

---

### Task 7: Local run tooling (env example, DB setup, uvicorn runner)

Port the reusable baremetal run tooling from `~/dev/endurain/local/` into `endurain-fork/local/`, adjusting paths to point at `endurain-fork/build/` (produced by Task 8). These files are largely copy-with-path-tweaks; they do NOT depend on the homelab repo.

**Files:**
- Create: `/home/brian/dev/endurain-fork/local/env.local.example`
- Create: `/home/brian/dev/endurain-fork/local/setup-db.sh`
- Create: `/home/brian/dev/endurain-fork/local/run.sh`
- Reference (read-only): `/home/brian/dev/endurain/local/{env.local.example,setup-db.sh,run.sh}`

**Interfaces:**
- Consumes: `build/app/` and `build/frontend/` produced by `build.sh` (Task 8): `build/app/.venv/bin/uvicorn`, `build/app/main.py`, `build/frontend/`.
- Produces: runnable local server on `:8080`. `run.sh` loads `local/.env`, exports `BACKEND_DIR`/`FRONTEND_DIR`/`DATA_DIR`/`LOGS_DIR`, and execs uvicorn from `build/app`.

- [ ] **Step 1: Copy env example**

Copy `/home/brian/dev/endurain/local/env.local.example` to `/home/brian/dev/endurain-fork/local/env.local.example` verbatim (it has no repo-specific paths — directories are auto-filled by `run.sh`).

- [ ] **Step 2: Copy setup-db.sh**

Copy `/home/brian/dev/endurain/local/setup-db.sh` to `/home/brian/dev/endurain-fork/local/setup-db.sh` verbatim (it reads `local/.env` relative to its own location; no changes needed). Ensure executable:
```bash
chmod +x /home/brian/dev/endurain-fork/local/setup-db.sh
```

- [ ] **Step 3: Write run.sh pointing at endurain-fork/build/**

Create `/home/brian/dev/endurain-fork/local/run.sh` (adapted from the reference — the only difference is `BUILD` is one level up from `local/`, i.e. the repo root's `build/`):
```bash
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
```
Make executable:
```bash
chmod +x /home/brian/dev/endurain-fork/local/run.sh
```

- [ ] **Step 4: Verify scripts are syntactically valid bash**

Run:
```bash
bash -n /home/brian/dev/endurain-fork/local/run.sh && bash -n /home/brian/dev/endurain-fork/local/setup-db.sh && echo "bash syntax OK"
```
Expected: `bash syntax OK`.

- [ ] **Step 5: Commit**

```bash
cd /home/brian/dev/endurain-fork
git add local/env.local.example local/setup-db.sh local/run.sh
git commit -m "feat: add local baremetal run tooling (env, db setup, runner)"
```

---

### Task 8: `build.sh` — compose pristine upstream + patches + overlay

The core deliverable: fetch pristine upstream at `upstream.ref`, apply `patches/` in `series` order, copy `overlay/`, then build frontend + backend into `build/`. Replaces the homelab role's ~30 brittle patch tasks with one composed step driven by this repo.

**Files:**
- Create: `/home/brian/dev/endurain-fork/build.sh`
- Reference (read-only): `/home/brian/dev/endurain/local/build.sh` (build phases, venv/export approach)
- Consumes: `upstream.ref`, `patches/series` + `patches/*.patch`, `overlay/**`

**Interfaces:**
- Consumes: Tasks 1-6 outputs (ref, patches, series, overlay).
- Produces: `build/source/` (composed tree), `build/app/` (backend runtime + `.venv`), `build/frontend/` (built SPA dist). These are what `local/run.sh` (Task 7) consumes: `build/app/.venv/bin/uvicorn`, `build/app/main.py`, `build/frontend/`.

Build phases (mirroring reference `local/build.sh` but sourcing patches/overlay from THIS repo):
1. Read `upstream.ref`. Fetch pristine upstream into `build/source/` — clone `github.com/endurain-project/endurain` at that ref (shallow), or reuse a local mirror if `ENDURAIN_UPSTREAM_DIR` is set (defaults to `/home/brian/dev/endurain`), copying its tree excluding `.git`/`local`/`node_modules`/`dist`/`__pycache__`.
2. Apply each patch in `patches/series` via `git apply` from `build/source/` (init a throwaway git index there so `git apply` works, or use `git apply --unsafe-paths -p1` with `--directory`). Fail loudly naming the patch if it doesn't apply.
3. Copy `overlay/backend/**` and `overlay/frontend/**` over `build/source/`.
4. Build frontend: `cd build/source/frontend/app && npm ci && NODE_OPTIONS=--max-old-space-size=2048 npm run build`; copy `dist` -> `build/frontend/`; write `env.js`.
5. Build backend: `cp -a build/source/backend/app/. build/app/`; create venv with `uv`; export deps from upstream `pyproject`/`poetry.lock` and install; install `shapely pyproj`.

- [ ] **Step 1: Write build.sh**

Create `/home/brian/dev/endurain-fork/build.sh`:
```bash
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
```
Make executable:
```bash
chmod +x /home/brian/dev/endurain-fork/build.sh
```

- [ ] **Step 2: Verify bash syntax**

Run:
```bash
bash -n /home/brian/dev/endurain-fork/build.sh && echo "bash syntax OK"
```
Expected: `bash syntax OK`.

- [ ] **Step 3: Dry-run phases 1-3 only (compose without building) to validate patches+overlay apply**

Temporarily verify the compose half end-to-end by running the real script but interrupting before the slow npm/uv steps is not clean; instead validate phases 1-3 manually:
```bash
cd /home/brian/dev/endurain-fork
rm -rf /tmp/opencode/efork-compose && mkdir -p /tmp/opencode/efork-compose
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C /tmp/opencode/efork-compose -xf -
cd /tmp/opencode/efork-compose && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; \
  git apply --whitespace=nowarn "/home/brian/dev/endurain-fork/patches/$p" && echo "applied $p" || { echo "FAILED $p"; break; }; \
done < /home/brian/dev/endurain-fork/patches/series
cp -a /home/brian/dev/endurain-fork/overlay/backend/. backend/
cp -a /home/brian/dev/endurain-fork/overlay/frontend/. frontend/
python3 -c "import ast; ast.parse(open('backend/app/main.py').read()); ast.parse(open('backend/app/core/scheduler.py').read()); print('backend patched files parse OK')"
test -f backend/app/custom/__init__.py && test -f backend/app/trail_matcher.py && test -f backend/app/pg18_compat.py && echo "overlay backend present"
test -f frontend/app/src/components/Activities/ActivitySegmentsLapsComponent.vue && echo "overlay frontend present"
```
Expected: each patch prints `applied ...`, then `backend patched files parse OK`, `overlay backend present`, `overlay frontend present`.

- [ ] **Step 4: Full build (produces runnable tree)**

Run:
```bash
cd /home/brian/dev/endurain-fork && ./build.sh
```
Expected: completes through `[6/6] Build complete.` with `build/app/.venv/bin/uvicorn` present. (Requires network for npm/uv, Node available. If Node version errors occur, install Node 24 — homelab pins it.)

- [ ] **Step 5: Verify build artifacts**

Run:
```bash
test -x /home/brian/dev/endurain-fork/build/app/.venv/bin/uvicorn && \
test -f /home/brian/dev/endurain-fork/build/app/main.py && \
test -d /home/brian/dev/endurain-fork/build/frontend && \
test -f /home/brian/dev/endurain-fork/build/app/custom/__init__.py && \
echo "BUILD ARTIFACTS OK"
```
Expected: `BUILD ARTIFACTS OK`.

- [ ] **Step 6: Commit**

```bash
cd /home/brian/dev/endurain-fork
git add build.sh
git commit -m "feat: add build.sh compose (upstream + patches + overlay -> build/)"
```

---

### Task 9: End-to-end smoke test (composed server boots against local Postgres)

Prove the whole pipeline: composed build + custom layer + PG18 compat actually starts and serves, and that upstream sync is gated off. This is the acceptance test for "runnable on localhost baremetal."

**Files:**
- Create: `/home/brian/dev/endurain-fork/local/.env` (from example; gitignored — NOT committed)
- Consumes: Task 8 build output, Task 7 run tooling.

**Interfaces:**
- Consumes: `build/app/.venv/bin/uvicorn`, a running local Postgres.
- Produces: verified HTTP 200 from `/api/v1/public/server_settings` and a reachable custom endpoint.

- [ ] **Step 1: Create local env with real secrets**

Run:
```bash
cd /home/brian/dev/endurain-fork
cp local/env.local.example local/.env
python3 -c "import secrets; print('SECRET_KEY='+secrets.token_hex(32))"
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY='+Fernet.generate_key().decode())"
```
Edit `local/.env`: set `SECRET_KEY` and `FERNET_KEY` to the generated values, and set `DB_PASSWORD` to a chosen local password. Confirm `ENDURAIN_UPSTREAM_SYNC=false`.

- [ ] **Step 2: Create the local DB role + database**

Run (requires local Postgres 18 reachable as superuser `postgres`):
```bash
cd /home/brian/dev/endurain-fork && ./local/setup-db.sh
```
Expected: `Ensuring role 'endurain'`, `Ensuring database 'endurain'`, `Done.`

- [ ] **Step 3: Start the server in the background**

Run:
```bash
cd /home/brian/dev/endurain-fork
( ./local/run.sh > /tmp/opencode/efork-run.log 2>&1 & echo $! > /tmp/opencode/efork.pid )
sleep 20
```
Expected: process started; log shows uvicorn startup and the custom-layer log lines `Custom migrations applied successfully` and `Custom API routes registered` (from `custom/__init__.py`), plus `Upstream sync disabled (ENDURAIN_UPSTREAM_SYNC != true)`.

- [ ] **Step 4: Verify upstream health endpoint responds**

Run:
```bash
curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/v1/public/server_settings
```
Expected: `200`.

- [ ] **Step 5: Verify the custom API is mounted**

Run:
```bash
curl -fsS -o /dev/null -w "%{http_code}\n" "http://localhost:8080/api/v1/custom/activities/by-garmin/1/segments"
```
Expected: `200` (empty list `[]` for an unknown Garmin ID — proves the custom router + `_resolve_garmin` path is live, not a 404).

- [ ] **Step 6: Confirm upstream-sync gate is respected in logs**

Run:
```bash
grep -c "Upstream sync disabled" /tmp/opencode/efork-run.log
```
Expected: `>= 1`. (No Strava/Garmin polling attempted.)

- [ ] **Step 7: Stop the server**

Run:
```bash
kill "$(cat /tmp/opencode/efork.pid)" 2>/dev/null; rm -f /tmp/opencode/efork.pid
echo "stopped"
```
Expected: `stopped`.

- [ ] **Step 8: Confirm .env is gitignored (not accidentally committed)**

Run:
```bash
cd /home/brian/dev/endurain-fork && git status --porcelain local/.env
```
Expected: no output (ignored). If it shows `?? local/.env`, the `.gitignore` from Task 1 is wrong — fix before proceeding.

- [ ] **Step 9: Final commit (docs/state only; no secrets)**

```bash
cd /home/brian/dev/endurain-fork
git add -A
git status   # confirm local/.env and build/ are NOT staged
git commit -m "test: verified composed baremetal build boots + custom layer live" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Standalone repo that owns the customizations → Task 1 (skeleton), Tasks 2-6 (content). ✔
- Distro-patchset model (pinned ref + patches + overlay) → `upstream.ref` (T1), `patches/` (T3-6), `overlay/` (T2). ✔
- Patches as real unified diffs via `git apply` + `series` → T3-6 generate diffs, verify clean apply; `build.sh` applies in series order (T8). ✔
- Convert `.vue` diffs to patches, keep genuinely-new as overlay → classification enforced: T2 Step 6 asserts overlay files are NOT upstream; T5/T6 patch the two upstream `.vue` files. ✔
- Runnable on localhost baremetal → T7 (run tooling) + T8 (build) + T9 (e2e boot). ✔
- Committable independently → per-task commits throughout. ✔
- Homelab pulls this repo to install → out of scope for THIS plan by design (separate later plan; noted in Global Constraints). The repo is *shaped* for consumption (`build.sh` is the single entrypoint). ✔
- Reuse `local/` where reusable, clean implementation → T7/T8 port `local/` logic; patches regenerated as real diffs rather than string-surgery scripts. ✔
- Don't touch homelab / respect active agent → Global Constraints + no homelab writes in any task. ✔

**Placeholder scan:** No TODO/TBD; every code step contains literal file content or exact commands. Patch *bodies* are generated (not hand-written) from verified upstream + documented edit definitions, with a clean-apply gate on each — this is deliberate (a diff is derived, not authored) and each has a concrete verification command.

**Type/interface consistency:** `build/app/`, `build/frontend/`, `build/app/.venv/bin/uvicorn` are named identically across T7 (run.sh consumes), T8 (build.sh produces), T9 (smoke test). `patches/series` line format (bare filename) consistent T3-6 (append) and T8 (read). Overlay destination `backend/app/custom/` consistent T2 (produce) and T8 Step 3 / T9 (consume).

**Open risk flagged for executor:** T6 Step 2 — if `patch_activity_view.py` prints any `WARNING: Could not find`, the ActivityView anchors have drifted vs upstream v0.17.7; STOP and reconcile before trusting patch 0004. Same for T5 Step 1 (anchor count must be 1).

## Execution Handoff

Plan complete and saved to `/home/brian/dev/endurain-fork/docs/plans/2026-07-24-endurain-fork-extraction.md`.
