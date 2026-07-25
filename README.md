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

After a bump, always smoke-test the running server (`./local/run.sh`, then hit
`/api/v1/public/server_settings` and a `/api/v1/custom/...` route). Patches fail
loudly when upstream moves, but `overlay/` files are copied verbatim — if
upstream changes a prop, API shape, or stream-type convention an overlay relies
on, the build still succeeds and only breaks at runtime.
