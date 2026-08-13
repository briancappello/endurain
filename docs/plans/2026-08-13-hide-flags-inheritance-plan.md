# Per-activity `hide_*` inheritance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 12 per-activity `hide_*` flags support `NULL` = inherit the owner's global `users_privacy_settings.hide_activity_*`, with explicit `true`/`false` as a per-activity override.

**Architecture:** Store each flag under an underscored column attribute (`_hide_X = Column("hide_X", nullable=True)`, DB name unchanged) and expose the public `hide_X` as a SQLAlchemy `hybrid_property` that resolves `NULL` to the owner's global. A relationship `user_privacy_settings` on `Activity` reaches the global row. All 43 upstream read sites read `activity.hide_X` and transparently get the resolved value; nothing filters `hide_*` in SQL, so no hybrid `.expression` is needed. Import stops stamping globals so new activities are `NULL`. A new `ActivityRaw` schema is the sole write body and the response of a new `GET /activities/{id}/raw`, so writes always speak raw tri-state.

**Tech Stack:** Python 3.13, SQLAlchemy (declarative + hybrid_property), FastAPI, Pydantic v2, Alembic (custom tree), Vue 3, PostgreSQL.

## Global Constraints

- **Patch-tree model.** Files under `build/source/backend/app/**` and `build/source/frontend/app/**` are UPSTREAM — edits to them ship as unified-diff **patches** in `patches/` (added to `patches/series`), regenerated via: apply series into a pristine `v0.17.7` scratch tree, edit, `git diff HEAD~1 -- <file>`. Files under `overlay/backend/app/**` and `overlay/frontend/app/**` are custom — edit directly, no patch.
- **Upstream pinned ref:** `v0.17.7` (see `upstream.ref`).
- **The 12 flags (exact names):** `hide_start_time`, `hide_location`, `hide_map`, `hide_hr`, `hide_power`, `hide_cadence`, `hide_elevation`, `hide_speed`, `hide_pace`, `hide_laps`, `hide_workout_sets_steps`, `hide_gear`.
- **Global counterparts:** on `users_privacy_settings` each flag is `hide_activity_<X>` (e.g. `hide_hr` → `hide_activity_hr`), model class `UsersPrivacySettings`, all `Mapped[bool]` `nullable=False default=False`.
- **`visibility` is OUT OF SCOPE** — leave it a hard `NOT NULL` integer, keep it stamped from `default_activity_visibility` at import, do not touch its SQL filters.
- **Test venv:** backend tests import `shapely`/`fastapi`/`sqlalchemy`; run with `build/app/.venv/bin/python`. Standalone tests (no pytest framework), assert-based, run as `build/app/.venv/bin/python tests/<file>.py`, matching `tests/test_api_privacy_gates.py`.
- **No fallback in the resolver.** Every user is guaranteed a `users_privacy_settings` row (UNIQUE+NOT NULL+FK CASCADE + signup creation + backfill). A missing row must raise, not silently default.
- **Migrations** live in the custom alembic tree `overlay/backend/app/custom/migrations/versions/`, sequential numeric revisions chained via `down_revision`, tracked in `custom_alembic_version`. Applied at startup by `custom.run_migrations()`.

---

### Task 1: Migration — make the 12 `hide_*` columns nullable

**Files:**
- Create: `overlay/backend/app/custom/migrations/versions/004_hide_flags_nullable.py`

**Interfaces:**
- Consumes: nothing (foundation task). Chains after existing revision `003`.
- Produces: `activities.hide_*` columns become `NULL`-able in the DB. No Python API.

**Why first:** every later task's runtime behavior depends on these columns being nullable. This is a custom-tree migration (no patch), applied at startup by `custom.run_migrations()` → `alembic upgrade head`.

- [ ] **Step 1: Confirm the current head revision is `003`**

Run: `ls overlay/backend/app/custom/migrations/versions/`
Expected: `001_activity_metadata.py  002_merge_columns.py  003_segments.py` — so the new revision's `down_revision = "003"`.

- [ ] **Step 2: Write the migration**

Create `overlay/backend/app/custom/migrations/versions/004_hide_flags_nullable.py`:

```python
"""Make per-activity hide_* columns nullable (NULL = inherit global)

Revision ID: 004
Revises: 003
Create Date: 2026-08-13

Drops the NOT NULL constraint on the 12 activities.hide_* columns so a NULL
value can mean "inherit the owner's global users_privacy_settings default".
Existing rows keep their current boolean, which now acts as an explicit
per-activity override.

ponytail: this custom-tree migration ALTERs an upstream table (activities). It
has a soft dependency on upstream's schema -- if a future upstream version drops
or renames a hide_* column, this migration and the model hybrids must be
refreshed against it.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HIDE_COLUMNS = (
    "hide_start_time",
    "hide_location",
    "hide_map",
    "hide_hr",
    "hide_power",
    "hide_cadence",
    "hide_elevation",
    "hide_speed",
    "hide_pace",
    "hide_laps",
    "hide_workout_sets_steps",
    "hide_gear",
)


def upgrade() -> None:
    for col in HIDE_COLUMNS:
        op.alter_column("activities", col, existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    # Backfill any NULLs to False before restoring NOT NULL, or the constraint
    # would fail on inherited rows.
    for col in HIDE_COLUMNS:
        op.execute(f"UPDATE activities SET {col} = false WHERE {col} IS NULL")
        op.alter_column("activities", col, existing_type=sa.Boolean(), nullable=False)
```

- [ ] **Step 3: Sync the overlay into the running build and restart to apply**

The running server applies custom migrations at startup. Sync the new migration file and restart (see Global Constraints for the restart gotcha — launch in its own call, poll separately):

Run: `cp overlay/backend/app/custom/migrations/versions/004_hide_flags_nullable.py build/app/custom/migrations/versions/`
Then restart: `pkill -f "uvicorn main:app"; sleep 2` (own call), then `nohup ./local/run.sh > /tmp/opencode/logs/endurain.log 2>&1 &` (own call), then poll: `sleep 8; curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://localhost:8080/`
Expected: `200`, and `/tmp/opencode/logs/endurain.log` shows migration ran without error.

- [ ] **Step 4: Verify the columns are now nullable**

Run:
```bash
set -a; . local/.env; set +a; export PGPASSWORD="$DB_PASSWORD"
psql -h localhost -U endurain -d endurain -tAF'|' -c "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='activities' AND column_name LIKE 'hide_%' ORDER BY column_name" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
```
Expected: all 12 rows show `|YES`.

- [ ] **Step 5: Verify custom_alembic_version advanced to 004**

Run: `psql -h localhost -U endurain -d endurain -tAc "SELECT version_num FROM custom_alembic_version" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"`
Expected: `004`

- [ ] **Step 6: Commit**

```bash
git add overlay/backend/app/custom/migrations/versions/004_hide_flags_nullable.py
git commit -m "feat: migration to make activity hide_* columns nullable for inheritance"
```

---

### Task 2: Model — underscored columns, hybrid properties, relationship (patch upstream)

**Files:**
- Modify (patch): `build/source/backend/app/activities/activity/models.py`
- Test: `tests/test_hide_flags_resolver.py` (new, in repo `tests/`)
- Patch output: `patches/0007-hide-flags-hybrid.patch` + append to `patches/series`

**Interfaces:**
- Consumes: Task 1's nullable columns.
- Produces: on the `Activity` ORM model, for each of the 12 flags: a private column `_hide_X = Column("hide_X", Boolean, nullable=True)`, a `@hybrid_property hide_X -> bool` (getter resolves `NULL`→`self.user_privacy_settings.hide_activity_X`, setter writes `_hide_X`), and a `user_privacy_settings` relationship (`UsersPrivacySettings`, viewonly, uselist=False, selectin). `activity.hide_X` reads resolved; `activity._hide_X` reads raw; `activity.hide_X = v` writes raw.

**Note on the test:** the resolver is pure Python (no SQL). Test it with a lightweight fake — a bare object with `_hide_X` set and a stub `user_privacy_settings` — so no DB or SQLAlchemy session is needed. This mirrors the standalone style of `tests/test_api_privacy_gates.py`. `Activity.__new__(Activity)` bypasses `__init__`; setting `_hide_hr` and `user_privacy_settings` as plain instance attributes and then reading the hybrid does NOT trigger SQLAlchemy mapper configuration (the getter is plain Python touching instance attrs), so no `configure_mappers()` call is needed. If SQLAlchemy ever raises a mapper-config error on import, the string-ref relationships (`"Users"`, `"Gear"`, `"UsersPrivacySettings"`) are the cause — they resolve lazily and are never triggered by this test's attribute access.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hide_flags_resolver.py`:

```python
#!/usr/bin/env python3
"""Unit test for the Activity hide_* inheritance resolver.

The resolver is pure Python (nothing filters hide_* in SQL), so we test the
hybrid_property's getter/setter logic directly against a fake object -- no DB,
no SQLAlchemy session. Run with the build venv:
    build/app/.venv/bin/python tests/test_hide_flags_resolver.py
"""

import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "build" / "source" / "backend" / "app"
sys.path.insert(0, str(SRC))

# core.database.Base is needed at import time by the model module.
core = types.ModuleType("core")
core_database = types.ModuleType("core.database")
from sqlalchemy.orm import declarative_base  # noqa: E402

core_database.Base = declarative_base()
core.database = core_database
sys.modules["core"] = core
sys.modules["core.database"] = core_database

import activities.activity.models as models  # noqa: E402

Activity = models.Activity


class _FakeGlobal:
    """Stub users_privacy_settings row."""

    def __init__(self, hide_activity_hr):
        self.hide_activity_hr = hide_activity_hr


def _fake_activity(raw_hide_hr, global_hide_hr):
    """A bare Activity with only what the hide_hr hybrid getter touches."""
    a = Activity.__new__(Activity)          # bypass __init__/DB
    a._hide_hr = raw_hide_hr
    a.user_privacy_settings = _FakeGlobal(global_hide_hr)
    return a


def test_null_inherits_true_global():
    a = _fake_activity(raw_hide_hr=None, global_hide_hr=True)
    assert a.hide_hr is True, a.hide_hr


def test_null_inherits_false_global():
    a = _fake_activity(raw_hide_hr=None, global_hide_hr=False)
    assert a.hide_hr is False, a.hide_hr


def test_explicit_true_overrides_false_global():
    a = _fake_activity(raw_hide_hr=True, global_hide_hr=False)
    assert a.hide_hr is True, a.hide_hr


def test_explicit_false_overrides_true_global():
    a = _fake_activity(raw_hide_hr=False, global_hide_hr=True)
    assert a.hide_hr is False, a.hide_hr


def test_setter_writes_raw_column():
    a = Activity.__new__(Activity)
    a.hide_hr = None
    assert a._hide_hr is None, a._hide_hr
    a.hide_hr = True
    assert a._hide_hr is True, a._hide_hr
    a.hide_hr = False
    assert a._hide_hr is False, a._hide_hr


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `build/app/.venv/bin/python tests/test_hide_flags_resolver.py`
Expected: FAIL — `AttributeError` or assertion error, because `hide_hr` is currently a plain `NOT NULL` column, not a hybrid, and `_hide_hr` does not exist.

- [ ] **Step 3: Edit the model in the scratch tree (set up patch flow)**

Set up a scratch tree with the current series applied (this is the patch-regeneration flow from Global Constraints):

```bash
SCRATCH=/tmp/opencode/hideflags-model
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$SCRATCH" -xf -
cd "$SCRATCH" && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
git add -A && git -c user.email=b@x -c user.name=b commit -qm "series applied"
```

- [ ] **Step 4: Add the hybrid import to models.py**

Edit `/tmp/opencode/hideflags-model/backend/app/activities/activity/models.py`. Change the import block at the top (currently lines 1-13) to add the `hybrid_property` import:

```python
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    DECIMAL,
    BigInteger,
    Boolean,
    JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from core.database import Base
```

- [ ] **Step 5: Replace the 12 column definitions with underscored columns**

Replace each of the 12 `hide_X = Column(Boolean, nullable=False, [default=...], comment="...")` blocks (currently lines 147-208) with the underscored form. Keep the DB column name via the positional `"hide_X"` first arg, and make it nullable. Example for all 12 (apply the same pattern to each):

```python
    _hide_start_time = Column("hide_start_time", Boolean, nullable=True, comment="Hide activity start time")
    _hide_location = Column("hide_location", Boolean, nullable=True, comment="Hide activity location")
    _hide_map = Column("hide_map", Boolean, nullable=True, comment="Hide activity map")
    _hide_hr = Column("hide_hr", Boolean, nullable=True, comment="Hide activity heart rate")
    _hide_power = Column("hide_power", Boolean, nullable=True, comment="Hide activity power")
    _hide_cadence = Column("hide_cadence", Boolean, nullable=True, comment="Hide activity cadence")
    _hide_elevation = Column("hide_elevation", Boolean, nullable=True, comment="Hide activity elevation")
    _hide_speed = Column("hide_speed", Boolean, nullable=True, comment="Hide activity speed")
    _hide_pace = Column("hide_pace", Boolean, nullable=True, comment="Hide activity pace")
    _hide_laps = Column("hide_laps", Boolean, nullable=True, comment="Hide activity laps")
    _hide_workout_sets_steps = Column("hide_workout_sets_steps", Boolean, nullable=True, comment="Hide activity workout sets and steps")
    _hide_gear = Column("hide_gear", Boolean, nullable=True, comment="Hide activity gear")
```

- [ ] **Step 6: Add the relationship after the existing `users` relationship**

Immediately after `users = relationship("Users", back_populates="activities")` (currently line 221), add:

```python
    # NULL per-activity hide_* inherits from the owner's global privacy row.
    # viewonly: we never write the global through here. selectin: batch-load the
    # owner's settings for feed lists, avoiding an N+1 across the resolver.
    user_privacy_settings = relationship(
        "UsersPrivacySettings",
        primaryjoin="Activity.user_id == foreign(UsersPrivacySettings.user_id)",
        viewonly=True,
        uselist=False,
        lazy="selectin",
    )
```

- [ ] **Step 7: Add the 12 hybrid properties (getter + setter) at the end of the class body**

After the last relationship in the class (after `activity_media = relationship(...)`, currently ends ~line 258), add the 12 hybrids. Pattern for each flag (shown for `hide_hr`; repeat for all 12, substituting the name and its `hide_activity_X` global):

```python
    # --- hide_* inheritance hybrids ---------------------------------------
    # getter: raw value if set, else inherit the owner's global. No fallback --
    # every user is guaranteed a user_privacy_settings row, so a missing one is
    # a broken invariant we fail loud on. No bool() -- both branches are bool.
    # No .expression: nothing filters hide_* in SQL; add one only if that changes.

    @hybrid_property
    def hide_start_time(self) -> bool:
        if self._hide_start_time is not None:
            return self._hide_start_time
        return self.user_privacy_settings.hide_activity_start_time

    @hide_start_time.setter
    def hide_start_time(self, value: bool | None) -> None:
        self._hide_start_time = value

    @hybrid_property
    def hide_location(self) -> bool:
        if self._hide_location is not None:
            return self._hide_location
        return self.user_privacy_settings.hide_activity_location

    @hide_location.setter
    def hide_location(self, value: bool | None) -> None:
        self._hide_location = value

    @hybrid_property
    def hide_map(self) -> bool:
        if self._hide_map is not None:
            return self._hide_map
        return self.user_privacy_settings.hide_activity_map

    @hide_map.setter
    def hide_map(self, value: bool | None) -> None:
        self._hide_map = value

    @hybrid_property
    def hide_hr(self) -> bool:
        if self._hide_hr is not None:
            return self._hide_hr
        return self.user_privacy_settings.hide_activity_hr

    @hide_hr.setter
    def hide_hr(self, value: bool | None) -> None:
        self._hide_hr = value

    @hybrid_property
    def hide_power(self) -> bool:
        if self._hide_power is not None:
            return self._hide_power
        return self.user_privacy_settings.hide_activity_power

    @hide_power.setter
    def hide_power(self, value: bool | None) -> None:
        self._hide_power = value

    @hybrid_property
    def hide_cadence(self) -> bool:
        if self._hide_cadence is not None:
            return self._hide_cadence
        return self.user_privacy_settings.hide_activity_cadence

    @hide_cadence.setter
    def hide_cadence(self, value: bool | None) -> None:
        self._hide_cadence = value

    @hybrid_property
    def hide_elevation(self) -> bool:
        if self._hide_elevation is not None:
            return self._hide_elevation
        return self.user_privacy_settings.hide_activity_elevation

    @hide_elevation.setter
    def hide_elevation(self, value: bool | None) -> None:
        self._hide_elevation = value

    @hybrid_property
    def hide_speed(self) -> bool:
        if self._hide_speed is not None:
            return self._hide_speed
        return self.user_privacy_settings.hide_activity_speed

    @hide_speed.setter
    def hide_speed(self, value: bool | None) -> None:
        self._hide_speed = value

    @hybrid_property
    def hide_pace(self) -> bool:
        if self._hide_pace is not None:
            return self._hide_pace
        return self.user_privacy_settings.hide_activity_pace

    @hide_pace.setter
    def hide_pace(self, value: bool | None) -> None:
        self._hide_pace = value

    @hybrid_property
    def hide_laps(self) -> bool:
        if self._hide_laps is not None:
            return self._hide_laps
        return self.user_privacy_settings.hide_activity_laps

    @hide_laps.setter
    def hide_laps(self, value: bool | None) -> None:
        self._hide_laps = value

    @hybrid_property
    def hide_workout_sets_steps(self) -> bool:
        if self._hide_workout_sets_steps is not None:
            return self._hide_workout_sets_steps
        return self.user_privacy_settings.hide_activity_workout_sets_steps

    @hide_workout_sets_steps.setter
    def hide_workout_sets_steps(self, value: bool | None) -> None:
        self._hide_workout_sets_steps = value

    @hybrid_property
    def hide_gear(self) -> bool:
        if self._hide_gear is not None:
            return self._hide_gear
        return self.user_privacy_settings.hide_activity_gear

    @hide_gear.setter
    def hide_gear(self, value: bool | None) -> None:
        self._hide_gear = value
```

- [ ] **Step 8: Regenerate the patch and add to series**

```bash
SCRATCH=/tmp/opencode/hideflags-model
cd "$SCRATCH"
git diff HEAD~1 -- backend/app/activities/activity/models.py > /home/brian/dev/endurain-fork/patches/0007-hide-flags-hybrid.patch
head -1 /home/brian/dev/endurain-fork/patches/0007-hide-flags-hybrid.patch   # sanity: a/backend/... header
```
Then append `0007-hide-flags-hybrid.patch` as a new final line in `patches/series` (Edit the file).

- [ ] **Step 9: Sync into build/app and run the resolver test**

The test imports from `build/source`, so also apply the model edit into `build/source` (copy from scratch tree) AND `build/app` (running code):

```bash
cp /tmp/opencode/hideflags-model/backend/app/activities/activity/models.py /home/brian/dev/endurain-fork/build/source/backend/app/activities/activity/models.py
cp /tmp/opencode/hideflags-model/backend/app/activities/activity/models.py /home/brian/dev/endurain-fork/build/app/activities/activity/models.py
find /home/brian/dev/endurain-fork/build/app -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
build/app/.venv/bin/python tests/test_hide_flags_resolver.py
```
Expected: `5 passed`.

- [ ] **Step 10: Verify the full patch series still applies clean against pristine**

```bash
V=/tmp/opencode/verify-0006; rm -rf "$V"; mkdir -p "$V"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$V" -xf -
cd "$V"; git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --check --whitespace=nowarn "$ROOT/patches/$p" && echo "OK $p" || echo "FAIL $p"; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
```
Expected: `OK` for all patches including `0007-hide-flags-hybrid.patch`.

- [ ] **Step 11: Commit**

```bash
git add patches/0007-hide-flags-hybrid.patch patches/series tests/test_hide_flags_resolver.py
git commit -m "feat: Activity hide_* hybrid properties resolving NULL to global settings"
```

---

### Task 3: Stop stamping globals at the 4 import sites (patch upstream)

**Files:**
- Modify (patch): `build/source/backend/app/fit/utils.py`, `build/source/backend/app/gpx/utils.py`, `build/source/backend/app/tcx/utils.py`, `build/source/backend/app/strava/activity_utils.py`
- Patch output: `patches/0008-drop-hide-stamping.patch` + append to `patches/series`

**Interfaces:**
- Consumes: Task 1 (columns nullable) + Task 2 (hybrids exist).
- Produces: newly imported activities have all 12 `_hide_*` = `NULL` (inherit). `user_privacy_settings` stays passed in and is still used for `visibility`.

**Change per file:** in each activity-construction call, DELETE the 12 lines that assign `hide_* = user_privacy_settings.hide_activity_* or False`. Do NOT delete the `visibility=...` line above them, and do NOT remove the `user_privacy_settings` parameter or its fetch — `visibility` still needs it.

The exact 12 kwargs to remove (they appear as a contiguous block in each constructor; some span two lines due to line-wrapping):

```python
        hide_start_time=user_privacy_settings.hide_activity_start_time or False,
        hide_location=user_privacy_settings.hide_activity_location or False,
        hide_map=user_privacy_settings.hide_activity_map or False,
        hide_hr=user_privacy_settings.hide_activity_hr or False,
        hide_power=user_privacy_settings.hide_activity_power or False,
        hide_cadence=user_privacy_settings.hide_activity_cadence or False,
        hide_elevation=user_privacy_settings.hide_activity_elevation or False,
        hide_speed=user_privacy_settings.hide_activity_speed or False,
        hide_pace=user_privacy_settings.hide_activity_pace or False,
        hide_laps=user_privacy_settings.hide_activity_laps or False,
        hide_workout_sets_steps=user_privacy_settings.hide_activity_workout_sets_steps or False,
        hide_gear=user_privacy_settings.hide_activity_gear or False,
```

Line references (indentation varies per file; delete the whole block including the 2-line wraps):
- `fit/utils.py`: 175-189 (deeper indent, inside `create_activity_objects`)
- `gpx/utils.py`: 366-378
- `tcx/utils.py`: 272-284
- `strava/activity_utils.py`: 342-354

- [ ] **Step 1: Set up the scratch tree with series applied (includes 0006)**

```bash
SCRATCH=/tmp/opencode/hideflags-stamping
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$SCRATCH" -xf -
cd "$SCRATCH" && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
git add -A && git -c user.email=b@x -c user.name=b commit -qm "series applied"
```

- [ ] **Step 2: Delete the 12 hide_* kwargs in each of the 4 files**

Edit each file in `/tmp/opencode/hideflags-stamping/backend/app/...`, removing exactly the block shown above (respecting each file's indentation and 2-line wraps). Leave the `visibility=...` kwarg intact.

- [ ] **Step 3: Verify each file still parses**

Run: `for f in fit/utils.py gpx/utils.py tcx/utils.py strava/activity_utils.py; do build/app/.venv/bin/python -m py_compile /tmp/opencode/hideflags-stamping/backend/app/$f && echo "OK $f"; done`
Expected: `OK` for all 4.

- [ ] **Step 4: Confirm no `hide_` kwargs remain in the 4 constructors**

Run: `grep -rn "hide_.*=user_privacy_settings" /tmp/opencode/hideflags-stamping/backend/app/fit /tmp/opencode/hideflags-stamping/backend/app/gpx /tmp/opencode/hideflags-stamping/backend/app/tcx /tmp/opencode/hideflags-stamping/backend/app/strava`
Expected: no matches.

- [ ] **Step 5: Confirm `visibility` stamping is still present**

Run: `grep -rln "visibility=.*default_activity_visibility\|visibility_to_int" /tmp/opencode/hideflags-stamping/backend/app/fit/utils.py /tmp/opencode/hideflags-stamping/backend/app/gpx/utils.py /tmp/opencode/hideflags-stamping/backend/app/tcx/utils.py /tmp/opencode/hideflags-stamping/backend/app/strava/activity_utils.py`
Expected: all 4 files listed.

- [ ] **Step 6: Regenerate the patch (multi-file diff) and add to series**

```bash
SCRATCH=/tmp/opencode/hideflags-stamping
cd "$SCRATCH"
git diff HEAD~1 -- backend/app/fit/utils.py backend/app/gpx/utils.py backend/app/tcx/utils.py backend/app/strava/activity_utils.py > /home/brian/dev/endurain-fork/patches/0008-drop-hide-stamping.patch
```
Append `0008-drop-hide-stamping.patch` as the final line of `patches/series`.

- [ ] **Step 7: Verify full series applies clean**

```bash
V=/tmp/opencode/verify-0007; rm -rf "$V"; mkdir -p "$V"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$V" -xf -
cd "$V"; git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --check --whitespace=nowarn "$ROOT/patches/$p" && echo "OK $p" || echo "FAIL $p"; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
```
Expected: `OK` for all, incl. `0007`.

- [ ] **Step 8: Sync the 4 files into build/app**

```bash
for f in fit/utils.py gpx/utils.py tcx/utils.py strava/activity_utils.py; do
  cp /tmp/opencode/hideflags-stamping/backend/app/$f /home/brian/dev/endurain-fork/build/app/$f
  cp /tmp/opencode/hideflags-stamping/backend/app/$f /home/brian/dev/endurain-fork/build/source/backend/app/$f
done
find /home/brian/dev/endurain-fork/build/app -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
```

- [ ] **Step 9: Commit**

```bash
git add patches/0008-drop-hide-stamping.patch patches/series
git commit -m "feat: stop stamping global hide_* onto new activities (insert NULL to inherit)"
```

> **Live import verification** (new activities insert NULL) is deferred to Task 7's end-to-end check, since it requires a running server with all backend tasks synced and a real Garmin/file import.

---

### Task 4: Rename `ActivityEdit` → `ActivityRaw` (patch upstream schema + its 2 references)

**Files:**
- Modify (patch): `build/source/backend/app/activities/activity/schema.py:83`, `build/source/backend/app/activities/activity/router.py:732`, `build/source/backend/app/activities/activity/crud.py:1246`
- Patch output: `patches/0009-activity-raw-schema.patch` + append to `patches/series`

**Interfaces:**
- Consumes: nothing new (pure rename; the existing `ActivityEdit` already declares all 12 `hide_*: bool | None`).
- Produces: `activities_schema.ActivityRaw` — the tri-state write body (`hide_* : bool | None`, `None`=inherit) and the response model for Task 5's `/raw` endpoint. `ActivityEdit` no longer exists.

**Why a rename, not a new class:** `ActivityEdit` already carries exactly the fields `ActivityRaw` needs (`id`, name/type/visibility + 12 `hide_*: bool | None`). Renaming keeps one write contract and avoids two near-identical schemas.

- [ ] **Step 1: Set up scratch tree with series applied (incl. 0006, 0007)**

```bash
SCRATCH=/tmp/opencode/hideflags-schema
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$SCRATCH" -xf -
cd "$SCRATCH" && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
git add -A && git -c user.email=b@x -c user.name=b commit -qm "series applied"
```

- [ ] **Step 2: Rename the class in schema.py**

In `/tmp/opencode/hideflags-schema/backend/app/activities/activity/schema.py`, change line 83:

```python
class ActivityEdit(BaseModel):
```
to:
```python
class ActivityRaw(BaseModel):
```

(Body unchanged — it already has `id`, name/type/visibility, and all 12 `hide_*: bool | None = None`.)

- [ ] **Step 3: Update the router reference**

In `/tmp/opencode/hideflags-schema/backend/app/activities/activity/router.py` line 732:

```python
    activity_attributes: activities_schema.ActivityEdit,
```
to:
```python
    activity_attributes: activities_schema.ActivityRaw,
```

- [ ] **Step 4: Update the crud reference**

In `/tmp/opencode/hideflags-schema/backend/app/activities/activity/crud.py` line 1246:

```python
    user_id: int, activity_attributes: activities_schema.ActivityEdit, db: Session
```
to:
```python
    user_id: int, activity_attributes: activities_schema.ActivityRaw, db: Session
```

- [ ] **Step 5: Verify no `ActivityEdit` references remain**

Run: `grep -rn "ActivityEdit" /tmp/opencode/hideflags-schema/backend/app/ 2>/dev/null | grep -v "__pycache__"`
Expected: no matches.

- [ ] **Step 6: Compile-check the three files**

Run: `for f in activities/activity/schema.py activities/activity/router.py activities/activity/crud.py; do build/app/.venv/bin/python -m py_compile /tmp/opencode/hideflags-schema/backend/app/$f && echo "OK $f"; done`
Expected: `OK` for all three.

- [ ] **Step 7: Regenerate patch + add to series**

```bash
cd /tmp/opencode/hideflags-schema
git diff HEAD~1 -- backend/app/activities/activity/schema.py backend/app/activities/activity/router.py backend/app/activities/activity/crud.py > /home/brian/dev/endurain-fork/patches/0009-activity-raw-schema.patch
```
Append `0009-activity-raw-schema.patch` to `patches/series`.

- [ ] **Step 8: Verify full series applies clean + sync to build**

```bash
V=/tmp/opencode/verify-0008; rm -rf "$V"; mkdir -p "$V"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$V" -xf -
cd "$V"; git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --check --whitespace=nowarn "$ROOT/patches/$p" && echo "OK $p" || echo "FAIL $p"; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
for f in activities/activity/schema.py activities/activity/router.py activities/activity/crud.py; do cp /tmp/opencode/hideflags-schema/backend/app/$f /home/brian/dev/endurain-fork/build/app/$f; cp /tmp/opencode/hideflags-schema/backend/app/$f /home/brian/dev/endurain-fork/build/source/backend/app/$f; done
```
Expected: `OK` for all patches.

- [ ] **Step 9: Commit**

```bash
git add patches/0009-activity-raw-schema.patch patches/series
git commit -m "refactor: rename ActivityEdit schema to ActivityRaw (tri-state write body)"
```

---

### Task 5: `GET /activities/{id}/raw` + raw crud builder (patch upstream)

**Files:**
- Modify (patch): `build/source/backend/app/activities/activity/crud.py` (add builder), `build/source/backend/app/activities/activity/router.py` (add endpoint)
- Test: `tests/test_activity_raw_builder.py` (new)
- Patch output: `patches/0010-activity-raw-endpoint.patch` + append to `patches/series`

**Interfaces:**
- Consumes: Task 2 (`_hide_*` raw columns), Task 4 (`ActivityRaw` schema).
- Produces:
  - `activities_crud.get_activity_raw_by_id(activity_id: int, user_id: int, db) -> activities_schema.ActivityRaw` — owner-only; returns the RAW `_hide_*` values (`None`/`True`/`False`), never resolved; raises 404 if the activity does not exist or is not owned by `user_id`.
  - `GET /activities/{activity_id}/raw` → `response_model=ActivityRaw`, scope `activities:read`, owner-only.

**Design point:** the builder reads `db_activity._hide_X` DIRECTLY (bypassing the hybrids) so the edit form sees the real setting. Owner-only (not visibility-based) because raw inheritance settings are private to the owner.

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_raw_builder.py`:

```python
#!/usr/bin/env python3
"""Unit test: the /raw builder returns RAW _hide_* values, never resolved.

Standalone, no DB: fakes a db.query(...).filter(...).first() chain that returns
a bare object carrying only _hide_* attrs, and asserts the built ActivityRaw
echoes them verbatim (None stays None even when a global would resolve True).
Run: build/app/.venv/bin/python tests/test_activity_raw_builder.py
"""

import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "build" / "source" / "backend" / "app"
sys.path.insert(0, str(SRC))

# crud pulls in many modules at import; stub the ones it needs at import time.
# We only exercise get_activity_raw_by_id, which uses activities_models,
# activities_schema, HTTPException/status, and Session typing.
# Rather than stub the whole world, import through a minimal shim: the test
# calls the builder with a fake db and a fake activity object, so only
# activities_schema.ActivityRaw must be importable for real.

# Minimal stubs for crud's import-time dependencies:
for name in (
    "core", "core.logger", "core.database", "core.sanitization",
    "followers", "followers.models", "gears", "gears.gear", "gears.gear.models",
    "users", "users.users", "users.users.models",
    "websocket", "websocket.manager",
):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["core.logger"].print_to_log = lambda *a, **k: None

# NOTE: if crud imports additional modules at load time, add them above. The
# goal is only to import the module so we can call the pure builder.

import activities.activity.crud as crud            # noqa: E402
import activities.activity.schema as schema        # noqa: E402


class _FakeActivity:
    def __init__(self, **raw):
        # id/name/type/visibility are required by ActivityRaw; give defaults.
        self.id = 5
        self.name = "x"
        self.activity_type = 1
        self.visibility = 0
        self.description = None
        self.private_notes = None
        self.is_hidden = False
        self.gear_id = None
        for k in (
            "hide_start_time", "hide_location", "hide_map", "hide_hr",
            "hide_power", "hide_cadence", "hide_elevation", "hide_speed",
            "hide_pace", "hide_laps", "hide_workout_sets_steps", "hide_gear",
        ):
            setattr(self, f"_{k}", raw.get(k, None))


class _Q:
    def __init__(self, obj):
        self._obj = obj
    def filter(self, *a, **k):
        return self
    def first(self):
        return self._obj


class _FakeDB:
    def __init__(self, obj):
        self._obj = obj
    def query(self, *a, **k):
        return _Q(self._obj)


def test_raw_builder_echoes_none_and_bools():
    act = _FakeActivity(hide_hr=None, hide_laps=True, hide_speed=False)
    db = _FakeDB(act)
    raw = crud.get_activity_raw_by_id(5, user_id=1, db=db)
    assert raw.hide_hr is None, raw.hide_hr        # inherit stays None, not resolved
    assert raw.hide_laps is True, raw.hide_laps
    assert raw.hide_speed is False, raw.hide_speed


def test_raw_builder_404_when_missing():
    db = _FakeDB(None)
    try:
        crud.get_activity_raw_by_id(5, user_id=1, db=db)
        raise AssertionError("expected HTTPException")
    except Exception as e:
        assert getattr(e, "status_code", None) == 404, e


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `build/app/.venv/bin/python tests/test_activity_raw_builder.py`
Expected: FAIL — `AttributeError: module 'activities.activity.crud' has no attribute 'get_activity_raw_by_id'`. (If import-time stubbing errors surface, add the missing module names to the stub loop in Step 1 — this is expected iteration for the standalone import.)

- [ ] **Step 3: Set up scratch tree (series incl. 0007-0009)**

```bash
SCRATCH=/tmp/opencode/hideflags-rawendpoint
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$SCRATCH" -xf -
cd "$SCRATCH" && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
git add -A && git -c user.email=b@x -c user.name=b commit -qm "series applied"
```

- [ ] **Step 4: Add the builder to crud.py**

In `/tmp/opencode/hideflags-rawendpoint/backend/app/activities/activity/crud.py`, add this function (place it right after `get_activity_by_id_from_user_id_or_has_visibility`, i.e. after its `except` block ~line 905):

```python
def get_activity_raw_by_id(activity_id: int, user_id: int, db: Session):
    """Owner-only. Return the RAW per-activity hide_* settings for editing.

    Reads the underscored columns directly, bypassing the resolving hybrids, so
    the edit form sees the actual setting: None (inherit) / True / False. Not
    visibility-gated -- raw inheritance settings are private to the owner.
    """
    activity = (
        db.query(activities_models.Activity)
        .filter(
            activities_models.Activity.user_id == user_id,
            activities_models.Activity.id == activity_id,
        )
        .first()
    )
    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found"
        )

    return activities_schema.ActivityRaw(
        id=activity.id,
        name=activity.name,
        activity_type=activity.activity_type,
        visibility=activity.visibility,
        is_hidden=activity.is_hidden,
        gear_id=activity.gear_id,
        description=activity.description,
        private_notes=activity.private_notes,
        hide_start_time=activity._hide_start_time,
        hide_location=activity._hide_location,
        hide_map=activity._hide_map,
        hide_hr=activity._hide_hr,
        hide_power=activity._hide_power,
        hide_cadence=activity._hide_cadence,
        hide_elevation=activity._hide_elevation,
        hide_speed=activity._hide_speed,
        hide_pace=activity._hide_pace,
        hide_laps=activity._hide_laps,
        hide_workout_sets_steps=activity._hide_workout_sets_steps,
        hide_gear=activity._hide_gear,
    )
```

- [ ] **Step 5: Add the endpoint to router.py**

In `/tmp/opencode/hideflags-rawendpoint/backend/app/activities/activity/router.py`, add after `read_activities_activity_from_id` (the `/{activity_id}` GET, ends ~line 582). IMPORTANT: register `/{activity_id}/raw` — FastAPI matches it fine alongside `/{activity_id}` since the suffix differs.

```python
@router.get(
    "/{activity_id}/raw",
    response_model=activities_schema.ActivityRaw,
)
async def read_activity_raw(
    activity_id: int,
    _validate_activity_id: Annotated[
        Callable, Depends(activities_dependencies.validate_activity_id)
    ],
    _check_scopes: Annotated[
        Callable, Security(auth_security.check_scopes, scopes=["activities:read"])
    ],
    token_user_id: Annotated[
        int,
        Depends(auth_security.get_sub_from_access_token),
    ],
    db: Annotated[
        Session,
        Depends(core_database.get_db),
    ],
):
    # Owner-only: raw hide_* settings for the edit form.
    return activities_crud.get_activity_raw_by_id(activity_id, token_user_id, db)
```

- [ ] **Step 6: Compile-check + regenerate patch**

```bash
for f in activities/activity/crud.py activities/activity/router.py; do build/app/.venv/bin/python -m py_compile /tmp/opencode/hideflags-rawendpoint/backend/app/$f && echo "OK $f"; done
cd /tmp/opencode/hideflags-rawendpoint
git diff HEAD~1 -- backend/app/activities/activity/crud.py backend/app/activities/activity/router.py > /home/brian/dev/endurain-fork/patches/0010-activity-raw-endpoint.patch
```
Append `0010-activity-raw-endpoint.patch` to `patches/series`.

- [ ] **Step 7: Verify series applies clean + sync to build**

```bash
V=/tmp/opencode/verify-0009; rm -rf "$V"; mkdir -p "$V"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$V" -xf -
cd "$V"; git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --check --whitespace=nowarn "$ROOT/patches/$p" && echo "OK $p" || echo "FAIL $p"; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
for f in activities/activity/crud.py activities/activity/router.py; do cp /tmp/opencode/hideflags-rawendpoint/backend/app/$f /home/brian/dev/endurain-fork/build/app/$f; cp /tmp/opencode/hideflags-rawendpoint/backend/app/$f /home/brian/dev/endurain-fork/build/source/backend/app/$f; done
find /home/brian/dev/endurain-fork/build/app -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
```
Expected: `OK` for all.

- [ ] **Step 8: Run the builder test**

Run: `build/app/.venv/bin/python tests/test_activity_raw_builder.py`
Expected: `2 passed`.

- [ ] **Step 9: Commit**

```bash
git add patches/0010-activity-raw-endpoint.patch patches/series tests/test_activity_raw_builder.py
git commit -m "feat: GET /activities/{id}/raw returning raw hide_* settings for editing"
```

---

### Task 6: Frontend — tri-state selects + load raw values (patch upstream)

**Files:**
- Modify (patch): `build/source/frontend/app/src/components/Activities/Modals/EditActivityModalComponent.vue`, `build/source/frontend/app/src/services/activitiesService.js`
- Patch output: `patches/0011-edit-activity-tristate.patch` + append to `patches/series`

**Interfaces:**
- Consumes: Task 5's `GET /activities/{id}/raw`.
- Produces: the edit modal shows a third option "Use global default" (value `null`) for each of the 12 `hide_*` selects, seeds them from `/raw` (raw values), and submits `null`/`true`/`false` via the existing `activities.editActivity(data)` PUT.

**Two mechanical changes:**
1. **Service**: add `getActivityRaw(activityId)` calling `GET activities/{id}/raw`.
2. **Modal**: (a) add a `null` option to each of the 12 selects; (b) when the modal opens, fetch `/raw` and seed the 12 `editActivityHide*` refs from it (instead of `props.activity.hide_*`, which is resolved).

- [ ] **Step 1: Set up scratch tree (series incl. 0007-0010)**

```bash
SCRATCH=/tmp/opencode/hideflags-frontend
rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$SCRATCH" -xf -
cd "$SCRATCH" && git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
git add -A && git -c user.email=b@x -c user.name=b commit -qm "series applied"
```

- [ ] **Step 2: Add the service method**

In `/tmp/opencode/hideflags-frontend/frontend/app/src/services/activitiesService.js`, add inside the exported object (next to `editActivity`, ~line 127):

```javascript
  getActivityRaw(activityId) {
    return fetchGetRequest(`activities/${activityId}/raw`)
  },
```
(Confirm `fetchGetRequest` is already imported at the top of the file; it is used by other methods there.)

- [ ] **Step 3: Add the third option to each of the 12 hide_* selects**

Each of the 12 selects currently reads:

```html
                <option :value="true">{{ $t('generalItems.yes') }}</option>
                <option :value="false">{{ $t('generalItems.no') }}</option>
```

Add a null option as the FIRST option in each (so "Use global default" shows on top). Repeat for all 12 selects (`activityHideStartTimeEdit`, `activityHideLocationEdit`, `activityHideMapEdit`, `activityHideHrEdit`, `activityHidePowerEdit`, `activityHideCadenceEdit`, `activityHideElevationEdit`, `activityHideSpeedEdit`, `activityHidePaceEdit`, `activityHideLapsEdit`, `activityHideWorkoutSetsStepsEdit`, `activityHideGearEdit`):

```html
                <option :value="null">{{ $t('editActivityModalComponent.hideUseGlobalDefault') }}</option>
                <option :value="true">{{ $t('generalItems.yes') }}</option>
                <option :value="false">{{ $t('generalItems.no') }}</option>
```

Also remove the `required` attribute from each of the 12 selects (a `null` value is now valid — `required` would reject it).

- [ ] **Step 4: Add the i18n string**

In `/tmp/opencode/hideflags-frontend/frontend/app/src/i18n/us/components/editActivityModalComponent.json` (US English source), add a key:

```json
  "hideUseGlobalDefault": "Use global default"
```
(Place it alongside the other `modalEditActivityHide*Label` keys. Other locales inherit English fallback; translating them is out of scope.)

- [ ] **Step 5: Seed the refs from /raw instead of props.activity**

The 12 refs currently init synchronously from `props.activity.hide_*` (resolved values), lines ~515-526. Change them to init `null` and populate from `/raw` when the modal opens.

Replace the 12 ref initializers (lines ~515-526) with:

```javascript
const editActivityHideStartTime = ref(null)
const editActivityHideLocation = ref(null)
const editActivityHideMap = ref(null)
const editActivityHideHr = ref(null)
const editActivityHidePower = ref(null)
const editActivityHideCadence = ref(null)
const editActivityHideElevation = ref(null)
const editActivityHideSpeed = ref(null)
const editActivityHidePace = ref(null)
const editActivityHideLaps = ref(null)
const editActivityHideWorkoutSetsSteps = ref(null)
const editActivityHideGear = ref(null)
```

Then add a loader. In the modal's `<script setup>`, import the service if not already imported (`import { activities } from '@/services/activitiesService'` — verify it's already imported; the modal already calls `activities.editActivity`). Add a function and call it when the modal is shown (the modal already wires a Bootstrap collapse listener near line 573; hook the same `shown.bs.modal` pattern, or call on mount if the modal instance is per-activity):

```javascript
async function loadRawHideSettings() {
  const raw = await activities.getActivityRaw(props.activity.id)
  if (!raw) return
  editActivityHideStartTime.value = raw.hide_start_time
  editActivityHideLocation.value = raw.hide_location
  editActivityHideMap.value = raw.hide_map
  editActivityHideHr.value = raw.hide_hr
  editActivityHidePower.value = raw.hide_power
  editActivityHideCadence.value = raw.hide_cadence
  editActivityHideElevation.value = raw.hide_elevation
  editActivityHideSpeed.value = raw.hide_speed
  editActivityHidePace.value = raw.hide_pace
  editActivityHideLaps.value = raw.hide_laps
  editActivityHideWorkoutSetsSteps.value = raw.hide_workout_sets_steps
  editActivityHideGear.value = raw.hide_gear
}

onMounted(loadRawHideSettings)
```
(Ensure `onMounted` is imported from `vue` at the top of the script — add it to the existing `import { ... } from 'vue'` line if absent.)

The `submitEditActivityForm` payload (lines ~538-549) already sends `hide_*: editActivityHide*.value` — now those carry `null`/`true`/`false`, which the backend persists correctly. No change needed there.

- [ ] **Step 6: Build the frontend to verify it compiles**

```bash
# copy the two edited files into build/source and build
cp /tmp/opencode/hideflags-frontend/frontend/app/src/components/Activities/Modals/EditActivityModalComponent.vue /home/brian/dev/endurain-fork/build/source/frontend/app/src/components/Activities/Modals/EditActivityModalComponent.vue
cp /tmp/opencode/hideflags-frontend/frontend/app/src/services/activitiesService.js /home/brian/dev/endurain-fork/build/source/frontend/app/src/services/activitiesService.js
cp /tmp/opencode/hideflags-frontend/frontend/app/src/i18n/us/components/editActivityModalComponent.json /home/brian/dev/endurain-fork/build/source/frontend/app/src/i18n/us/components/editActivityModalComponent.json
cd /home/brian/dev/endurain-fork/build/source/frontend/app
NODE_OPTIONS="--max-old-space-size=2048" npm run build 2>&1 | grep -E "built in|error|Error" | head
```
Expected: `✓ built in ...`, no errors.

- [ ] **Step 7: Deploy the fresh dist to the served dir**

```bash
cp -a /home/brian/dev/endurain-fork/build/source/frontend/app/dist/. /home/brian/dev/endurain-fork/build/frontend/
```

- [ ] **Step 8: Regenerate the patch (3 files) + add to series**

```bash
cd /tmp/opencode/hideflags-frontend
git diff HEAD~1 -- \
  frontend/app/src/components/Activities/Modals/EditActivityModalComponent.vue \
  frontend/app/src/services/activitiesService.js \
  frontend/app/src/i18n/us/components/editActivityModalComponent.json \
  > /home/brian/dev/endurain-fork/patches/0011-edit-activity-tristate.patch
```
Append `0011-edit-activity-tristate.patch` to `patches/series`.

- [ ] **Step 9: Verify full series applies clean**

```bash
V=/tmp/opencode/verify-0010; rm -rf "$V"; mkdir -p "$V"
git -C /home/brian/dev/endurain archive v0.17.7 | tar -C "$V" -xf -
cd "$V"; git init -q && git add -A && git -c user.email=b@x -c user.name=b commit -qm pristine
ROOT=/home/brian/dev/endurain-fork
while IFS= read -r p; do [ -z "$p" ] && continue; case "$p" in \#*) continue;; esac; git apply --check --whitespace=nowarn "$ROOT/patches/$p" && echo "OK $p" || echo "FAIL $p"; git apply --whitespace=nowarn "$ROOT/patches/$p"; done < "$ROOT/patches/series"
```
Expected: `OK` for all patches through `0010`.

- [ ] **Step 10: Commit**

```bash
git add patches/0011-edit-activity-tristate.patch patches/series
git commit -m "feat: tri-state hide_* controls in edit-activity modal (Use global default)"
```

---

### Task 7: End-to-end verification (live server)

**Files:** none (verification only). No commit unless a fix is needed.

**Interfaces:**
- Consumes: all of Tasks 1-6, synced into `build/app` + `build/frontend`.

**Purpose:** prove the whole inheritance flow works against the running server with real data. This is the integration gate the earlier per-task unit tests do not cover (resolver through the real ORM, real HTTP, real DB).

- [ ] **Step 1: Restart the server with all backend changes synced**

Ensure Tasks 1-5 synced their files into `build/app` and Task 6 into `build/frontend`. Restart (own call), then poll (separate call), per the restart gotcha:

```bash
pkill -f "uvicorn main:app"; sleep 2
```
then (own call): `nohup ./local/run.sh > /tmp/opencode/logs/endurain.log 2>&1 &`
then (own call): `sleep 8; curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://localhost:8080/`
Expected: `200`; log shows no migration/import errors.

- [ ] **Step 2: Run the full backend test suite (no regressions)**

```bash
V=build/app/.venv/bin/python
for t in test_trail_ranges test_trigger test_api_privacy_gates test_hide_flags_resolver test_activity_raw_builder; do
  echo "=== $t ==="; "$V" tests/$t.py 2>&1 | tail -1
done
```
Expected: each prints `N passed`, no tracebacks. (`test_api_privacy_gates` = 12, `test_trail_ranges` = 23, `test_trigger` = 5, plus the 2 new files.)

- [ ] **Step 3: Verify inheritance — global ON, activity NULL → hidden**

Pick an activity owned by user 1 with HR laps (e.g. id 2). Set its raw `_hide_hr` to NULL and the global ON:

```bash
set -a; . local/.env; set +a; export PGPASSWORD="$DB_PASSWORD"
psql -h localhost -U endurain -d endurain -c "UPDATE activities SET hide_hr = NULL WHERE id = 2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
psql -h localhost -U endurain -d endurain -c "UPDATE users_privacy_settings SET hide_activity_hr = true WHERE user_id = 1" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
curl -s --max-time 8 "http://localhost:8080/api/v1/public/custom/activities/2/laps" | python3 -c "import sys,json; d=json.load(sys.stdin); print('avg_hr all None:', all(l['avg_heart_rate'] is None for l in d))"
```
Expected: `avg_hr all None: True` — the NULL activity inherited the ON global.

- [ ] **Step 4: Verify inheritance — global ON, activity override FALSE → shown**

```bash
psql -h localhost -U endurain -d endurain -c "UPDATE activities SET hide_hr = false WHERE id = 2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
curl -s --max-time 8 "http://localhost:8080/api/v1/public/custom/activities/2/laps" | python3 -c "import sys,json; d=json.load(sys.stdin); print('any HR present:', any(l['avg_heart_rate'] is not None for l in d))"
```
Expected: `any HR present: True` — explicit False overrode the ON global.

- [ ] **Step 5: Verify override — activity TRUE, global OFF → hidden**

```bash
psql -h localhost -U endurain -d endurain -c "UPDATE users_privacy_settings SET hide_activity_hr = false WHERE user_id = 1" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
psql -h localhost -U endurain -d endurain -c "UPDATE activities SET hide_hr = true WHERE id = 2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
curl -s --max-time 8 "http://localhost:8080/api/v1/public/custom/activities/2/laps" | python3 -c "import sys,json; d=json.load(sys.stdin); print('avg_hr all None:', all(l['avg_heart_rate'] is None for l in d))"
```
Expected: `avg_hr all None: True` — explicit True overrode the OFF global.

- [ ] **Step 6: Verify the /raw endpoint returns raw values (authenticated, via browser session)**

Using Playwright with the logged-in admin session (or a minted token), GET `/api/v1/activities/2/raw` and confirm it returns `hide_hr: true` (the raw override), NOT a resolved value. In a Playwright `page.evaluate` against the authed session:

```javascript
async () => {
  const r = await fetch(`${window.env.ENDURAIN_HOST}/api/v1/activities/2/raw`, { credentials:'include' });
  return { status: r.status, body: await r.json() };
}
```
Expected: `status: 200`, `body.hide_hr === true`, and a field currently NULL (e.g. set `hide_laps = NULL` first) comes back `null` — proving raw, not resolved.

- [ ] **Step 7: Round-trip safety — GET /raw then PUT it back must not change stored values**

Set a known mix (`hide_hr = NULL`, `hide_laps = true`), GET `/raw`, PUT the same body via `activities/edit`, then re-read the DB:

```bash
psql -h localhost -U endurain -d endurain -c "UPDATE activities SET hide_hr = NULL, hide_laps = true WHERE id = 2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
```
Then via the authed browser session: GET `/activities/2/raw`, PUT the identical object to `activities/edit`. Re-read:
```bash
psql -h localhost -U endurain -d endurain -tAF'|' -c "SELECT hide_hr, hide_laps FROM activities WHERE id=2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
```
Expected: `|t` — `hide_hr` still NULL (renders empty), `hide_laps` still true. Proves inherit was NOT frozen into an override by the round-trip.

- [ ] **Step 8: Verify a freshly imported activity is NULL (Task 3 payoff)**

Import one activity (upload a GPX/FIT via the UI, or trigger a Garmin sync). Then:
```bash
psql -h localhost -U endurain -d endurain -tAF'|' -c "SELECT id, hide_hr, hide_laps, hide_map FROM activities ORDER BY id DESC LIMIT 1" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
```
Expected: the newest activity shows `||` (NULL) for the hide_* columns — inherited, not stamped.

- [ ] **Step 9: UI smoke test (Playwright, logged in)**

Open the edit-activity modal for activity 2. Confirm each hide_* select shows three options with "Use global default" present, and that the HR select reflects the raw stored value (from Step 7: `hide_hr` = "Use global default", `hide_laps` = "Yes"). Change one to "Use global default", save, reopen — confirm it persisted as inherit.

- [ ] **Step 10: Restore test data**

```bash
psql -h localhost -U endurain -d endurain -c "UPDATE activities SET hide_hr = NULL, hide_laps = NULL WHERE id = 2" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
psql -h localhost -U endurain -d endurain -c "UPDATE users_privacy_settings SET hide_activity_hr = false, hide_activity_laps = false WHERE user_id = 1" 2>/dev/null | grep -v "collation\|DETAIL\|HINT\|WARNING"
```

---

## Open verification items (from the spec — resolve during execution)

- **Profile-page "selection gone on refresh":** independently verify the profile GET/PUT round-trips `hide_activity_*`. The DB persists it (confirmed during design), so the symptom was likely "persisted but no visible effect" — which this feature fixes. If the profile GET genuinely drops the value, that is a separate frontend bug to file, not part of this plan.
- **No other write path sets `hide_*`:** `grep -rn "hide_hr=\|_hide_hr =" build/source/backend/app | grep -v .venv` after implementation should show only the model, the raw builder, and (removed) import sites — confirming the edit endpoint + import are the only writers.

