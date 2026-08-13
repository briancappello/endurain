# Per-activity `hide_*` inheritance from global privacy settings

Date: 2026-08-13
Status: approved (design), not yet implemented

## Problem

Endurain models 12 per-activity `hide_*` privacy flags on the `activities`
table (`hide_hr`, `hide_speed`, `hide_laps`, ...) and a parallel set of global
defaults on `users_privacy_settings` (`hide_activity_hr`, ...). The profile page
("myProfile" tab) edits the global defaults.

Two defects make the global settings misleading:

1. **Globals have no read-time effect.** They are read only at *import time* and
   stamped onto each new activity. Changing a global never affects any existing
   activity. The profile toggle appears global but behaves as a
   default-for-future-imports only.

2. **No "inherit" state exists.** The 12 `activities.hide_*` columns are
   `NOT NULL` booleans. Every activity carries a hard `true`/`false`, frozen at
   import. There is no way to say "defer to my global setting".

### Desired behavior

- Per-activity `hide_*` = **NULL** → inherit the owner's global
  `users_privacy_settings.hide_activity_*`.
- Per-activity `hide_*` = explicit **`true`/`false`** → per-activity override,
  wins over the global.
- A freshly imported activity has all `hide_*` = NULL (pure inherit).

`None`, `False`, `True` each mean exactly what their value says.

## Scope

**In scope:** the 12 `hide_*` flags on `activities`.

**Explicitly out of scope:**

- **`visibility`.** It is an integer access-control enum filtered in ~10 SQL
  `WHERE` clauses (`visibility == 0`, `.in_([0,1])`) across feeds and public
  gates. NULL-inherit would require rewriting every filter with a
  `COALESCE`/JOIN to the global — high blast radius and real leak risk (a missed
  filter = a private activity shown, or a public one 404'd). `visibility` stays
  a hard per-activity value, stamped from `default_activity_visibility` at
  import. Upstream's "Change activities visibility" bulk button remains the
  retroactive mechanism.
- The existing public **summary** metric leak (avg/max HR etc. shown on the
  public summary regardless of flags). That is upstream's
  `get_activity_by_id_if_is_public`; we accept upstream behavior there.
- The "Change activities visibility" bulk button (unchanged).

## Key facts established during design

- **Nothing filters `hide_*` in SQL.** All 43 upstream read sites
  (`activity_streams/crud.py`, `activity_laps/crud.py`, `activity_media/crud.py`)
  read `activity.hide_X` in Python *after* loading the row. This is what makes a
  Python-side hybrid property safe — no `.expression` needed.
- **Every user is guaranteed exactly one `users_privacy_settings` row.**
  Enforced by: `UNIQUE(user_id)` + `NOT NULL(user_id)` + FK `ON DELETE CASCADE`,
  a row created at signup (`users/users/utils.py:160`), and the `v0_12_0`
  migration backfilling existing users. So the resolver needs no null-guard
  fallback; a missing row is a broken invariant we fail loud on.
- **`hide_*` is written by exactly one endpoint.** `PUT /` (`edit_activity`),
  via the `ActivityEdit` body. The two `POST` create endpoints build the
  activity server-side from the uploaded file and never accept `hide_*` from a
  client body. `PUT /visibility/{visibility}` is visibility-only.
- **`edit_activity` is already tri-state safe.** It uses
  `model_dump(exclude_unset=True)` — it keys off field *presence*, not `None`.
  Absent = leave unchanged; present-as-null = set to inherit; present-as-bool =
  override.
- **Import stamps globals at 4 sites:** `fit/utils.py:175-189`,
  `gpx/utils.py`, `tcx/utils.py`, `strava/activity_utils.py`. Garmin ingest
  routes through the **FIT** site (`create_activity_objects`), so no
  Garmin-specific change is needed.

## Design

### 1. Data model (patch upstream `activities/activity/models.py`)

For each of the 12 flags, store the raw value under an underscored Python
attribute mapped to the **same DB column name**, and expose the public name as a
hybrid property:

```python
_hide_hr = Column("hide_hr", Boolean, nullable=True)  # DB column name unchanged

@hybrid_property
def hide_hr(self) -> bool:
    # NULL -> inherit owner's global; explicit True/False -> per-activity override.
    if self._hide_hr is not None:
        return self._hide_hr
    return self.user_privacy_settings.hide_activity_hr

@hide_hr.setter
def hide_hr(self, value: bool | None) -> None:
    self._hide_hr = value  # None = inherit, True/False = override
```

Notes:

- No `bool()` coercion: `_hide_hr` is `Optional[bool]` (returned directly when
  not None), and `hide_activity_hr` is a non-nullable `Mapped[bool]`. Both
  branches already yield a real `bool`.
- No fallback: relies on the guaranteed `user_privacy_settings` row. A missing
  row raises `AttributeError` — correct, fail loud.
- No `.expression`: nothing filters these in SQL. If a future SQL filter is
  added, that is when an `.expression` is added — mark with a `ponytail:` note.

Relationship, named after the model:

```python
user_privacy_settings = relationship(
    "UsersPrivacySettings",
    primaryjoin="Activity.user_id == foreign(UsersPrivacySettings.user_id)",
    viewonly=True,
    uselist=False,
    lazy="selectin",  # batch-loads owners for feed lists, avoids N+1
)
```

### 2. Migration (custom alembic tree)

New `overlay/backend/app/custom/migrations/versions/004_hide_flags_nullable.py`,
`Revises: "003"`, tracked in `custom_alembic_version`:

- `ALTER COLUMN hide_X DROP NOT NULL` for all 12 flags.
- No column renames (the model keeps the DB name via `Column("hide_X", ...)`).
- No data backfill: existing activities keep their current hard booleans, which
  now act as explicit overrides.
- `ponytail:` note: this migration alters an *upstream* table from the custom
  tree, so it has a soft dependency on upstream's `activities` schema.

**Existing 7 activities** stay as explicit overrides. Converting them to inherit
is an optional one-time `UPDATE activities SET hide_X = NULL` — opt-in, not part
of the migration (do not silently change existing privacy).

### 3. Write paths

- **Import stamping dropped** at all 4 sites (`fit`/`gpx`/`tcx`/`strava`
  utils): delete the 12 `hide_* = user_privacy_settings.hide_activity_X or False`
  lines so new activities insert with `_hide_* = NULL`. `user_privacy_settings`
  stays in each function — still needed for
  `visibility=visibility_to_int(user_privacy_settings.default_activity_visibility)`.
- **`ActivityEdit` → renamed `ActivityRaw`** (patch `schema.py`). Carries the 12
  `hide_*` as raw `bool | None`. It is the request body for `PUT /` (edit) — raw
  in.
- **Edit endpoint unchanged** beyond the schema rename: `exclude_unset` + the
  hybrid setter already persist `None`/`True`/`False` correctly.
- **Create endpoints unchanged** (file-driven; no `hide_*` in body).

### 4. Raw read endpoint

New owner-only `GET /activities/{id}/raw` (patch `router.py`) →
`response_model=ActivityRaw`. Symmetric with the write: raw out, raw in.

The crud builder reads the **raw underscore columns directly**
(`db_activity._hide_X`), bypassing the hybrids, so the edit form sees
`None`/`True`/`False` — the actual setting, not the resolved value. Explicit
builder (approach "b"), not Pydantic alias magic, so "we read raw on purpose" is
self-evident.

The existing `Activity` schema and `GET /activities/{id}` are **unchanged** —
they serialize the *resolved* hybrid values, which is correct for every
display/enforcement consumer.

### 5. Frontend (edit-activity modal)

Each `hide_*` on/off toggle becomes a **three-state select**:

- `Use global default` → `null`
- `Hidden` → `true`
- `Visible` → `false`

The modal loads from `GET /activities/{id}/raw` (raw values) and saves via
`PUT /` with the `ActivityRaw` body. `exclude_unset` means only changed fields
are sent.

## File surface & change classification

Patches to upstream (refresh on version bump):

| File | Change |
|---|---|
| `activities/activity/models.py` | 12× `_hide_X` column + hybrid getter/setter + `user_privacy_settings` relationship |
| `activities/activity/schema.py` | `ActivityEdit` → `ActivityRaw` (raw `bool \| None` ×12) |
| `activities/activity/router.py` | New owner-only `GET /activities/{id}/raw` → `ActivityRaw` |
| `activities/activity/crud.py` | Explicit builder reading `db_activity._hide_X` for the `/raw` response |
| `fit/utils.py`, `gpx/utils.py`, `tcx/utils.py`, `strava/activity_utils.py` | Delete 12 `hide_* = ...or False` stamping lines (keep `visibility`) |
| Frontend edit-activity modal | on/off toggles → tri-state selects |

Custom / overlay (no patch needed):

| File | Change |
|---|---|
| `custom/migrations/versions/004_hide_flags_nullable.py` | `ALTER COLUMN hide_X DROP NOT NULL` ×12, `Revises: "003"` |
| `custom/api.py` | No change — `_privacy_for` reads `activity.hide_X`, now resolved |

Untouched (the payoff): the 43 upstream read sites read `activity.hide_X` and
transparently get resolved values.

## Testing

- **Unit (model):** an `Activity` with `_hide_hr = None` and a stub
  `user_privacy_settings` returns the global; `_hide_hr = True/False` returns the
  override regardless of global; the setter writes `_hide_hr`.
- **Unit (raw builder):** `/raw` payload returns `None`/`True`/`False` verbatim
  from `_hide_X`, never resolved.
- **Migration:** columns are nullable after upgrade; a fresh insert without
  `hide_*` yields NULL.
- **Import:** a newly imported activity (all 4 paths, incl. Garmin/FIT) has
  `_hide_* = NULL`.
- **Enforcement parity (live):** set a global (e.g. `hide_activity_hr = true`)
  with the activity at NULL → public streams/laps/custom endpoints hide HR;
  set the activity's `hide_hr = false` → HR shows despite the global; set
  `hide_hr = true` → hidden despite a false global.
- **Round-trip safety:** GET `/raw` then PUT the same body must not change
  stored values (proves inherit is not frozen into an override).

## Open verification items (carry into implementation)

- Confirm the profile-page "selection gone on refresh" symptom: verify whether
  the profile GET/PUT round-trips `hide_activity_*` correctly, independent of
  this change. (The DB *does* persist the global; the perceived non-persistence
  may have been "persisted but no visible effect".)
- Confirm no other write path sets `hide_*` outside the 4 import sites + edit
  endpoint.
