# syntax=docker/dockerfile:1.7
# Endurain, composed from the patchset in THIS repo and built for production.
#
# This repo is a PATCHSET, not a checkout: it carries the upstream ref it
# targets, a series of patches, and an overlay of additional files. Stage 0
# turns those three into a source tree; stages 1-3 are upstream's own build,
# reading that tree instead of a checkout.
#
# The equivalent Ansible is homelab's roles/endurain/tasks/build.yml. Its
# idempotency stamp has no analogue here: a container build is idempotent by
# construction, which is most of the reason this exists.

# ---- Stage 0: compose -------------------------------------------------------
FROM docker.io/library/debian:bookworm-slim AS compose
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work

# READ, never hardcoded. The fork bumping its upstream must not require editing
# this file -- that is the whole point of the ref living in a tracked file.
COPY upstream.ref ./
RUN set -eux; \
    ref="$(tr -d '[:space:]' < upstream.ref)"; \
    echo "composing upstream ${ref}"; \
    mkdir -p /src; \
    curl -fsSL "https://github.com/endurain-project/endurain/archive/refs/tags/${ref}.tar.gz" \
      | tar -xz -C /src --strip-components=1

# git apply needs a repository, not merely a directory. The commit is throwaway
# scaffolding for the patch tool and never leaves this stage.
COPY patches ./patches
RUN set -eux; \
    cd /src; \
    git init -q; \
    git -c user.email=build@homelab -c user.name=homelab add -A; \
    git -c user.email=build@homelab -c user.name=homelab commit -qm pristine; \
    for p in $(grep -vE '^[[:space:]]*(#|$)' /work/patches/series); do \
      echo "applying ${p}"; \
      git apply --whitespace=nowarn "/work/patches/${p}"; \
    done

# The overlay lands AFTER the patches, and overwrites: patches edit upstream
# files, the overlay adds whole files (backend/app/custom/ is the module
# personal-site proxies). Reversing the order would let a patch context fail
# against a file the overlay had already replaced.
COPY overlay ./overlay
RUN set -eux; \
    cp -a /work/overlay/backend/. /src/backend/; \
    cp -a /work/overlay/frontend/. /src/frontend/

# ---- Stage 1: frontend ------------------------------------------------------
FROM docker.io/library/node:24.19.0-bookworm-slim AS frontend
WORKDIR /tmp/frontend
COPY --from=compose /src/frontend/app ./
RUN npm ci --prefer-offline
RUN npm run build
# -> /tmp/frontend/dist

# ---- Stage 2: requirements --------------------------------------------------
# Upstream ships poetry, not a requirements.txt. Exporting keeps the runtime
# image free of poetry itself.
FROM docker.io/library/python:3.13.14-slim-bookworm AS requirements
WORKDIR /tmp/backend
RUN pip install --no-cache-dir poetry \
 && poetry self add poetry-plugin-export
COPY --from=compose /src/backend/pyproject.toml /src/backend/poetry.lock* ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

# ---- Stage 3: runtime -------------------------------------------------------
FROM docker.io/library/python:3.13.14-slim-bookworm AS runtime

# gosu is how start.sh drops from root to UID:GID after fixing ownership on a
# freshly mounted volume. From apt and PINNED: upstream fetches "the latest
# release" from the GitHub API at build time, which is both unpinned and a
# network dependency in the runtime stage.
#
# libmagic1 is a FORK requirement upstream's Dockerfile does not carry: the
# custom file-upload layer (safeuploads -> python-magic) dlopen's libmagic at
# import, so without it the app crashes on startup with "failed to find
# libmagic". apt pulls libmagic-mgc (the magic database) as its dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gosu libmagic1 \
 && rm -rf /var/lib/apt/lists/* \
 && gosu nobody true

ENV UID=1000 \
    GID=1000 \
    BEHIND_PROXY=false

WORKDIR /app/frontend
COPY --from=frontend /tmp/frontend/dist ./dist

WORKDIR /app/backend
COPY --from=requirements /tmp/backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r ./requirements.txt

# NOT IN UPSTREAM'S LOCKFILE, and the app fails at import without them. The
# Ansible role installs the same two, for the same reason. Pinned like
# everything else; cp313 manylinux wheels exist for both, which is why this
# stage is Debian and not Alpine.
RUN pip install --no-cache-dir shapely==2.1.2 pyproj==3.7.2

COPY --from=compose /src/backend/app ./
COPY --from=compose /src/docker/start.sh /docker-entrypoint.d/start.sh
RUN chmod +x /docker-entrypoint.d/start.sh

# NON-ROOT BY DEFAULT. The paths start.sh writes are made the app user's here,
# at build time, so nothing needs root at run time: the data mount point (its
# contents come from the volume, group-owned through fsGroup), the logs
# directory, and env.js, which start.sh rewrites in place from ENDURAIN_HOST
# (the file, not its root-owned directory). Patch 0012 makes start.sh skip
# upstream's chown and gosu when it is not root.
RUN mkdir -p /app/backend/data /app/backend/logs \
 && touch /app/frontend/dist/env.js \
 && chown "${UID}:${GID}" /app/backend/data /app/backend/logs /app/frontend/dist/env.js
USER 1000:1000

EXPOSE 8080
ENTRYPOINT ["/docker-entrypoint.d/start.sh"]
