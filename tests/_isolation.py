"""Import a single overlay module under temporary stubs, without leaking them.

The pure-logic unit tests (trail_matcher, custom.api, custom.trigger) each need
to import ONE overlay module in isolation, with a handful of heavy dependencies
(``core``, ``core.logger``, ORM packages) replaced by lightweight stubs so the
import doesn't drag in SQLAlchemy/alembic/the whole app.

The old approach did this with bare module-level ``sys.path.insert`` +
``sys.modules[...] = stub`` statements. Under pytest those run at COLLECTION
time and mutate global state that never gets reverted, so a stub left by one
test file (e.g. a bare ``auth`` module with no ``__path__``) breaks a later
file's real import -- which is why conftest needed a purge hack.

``import_isolated`` fixes that at the source: it snapshots ``sys.path`` and
``sys.modules``, installs the stubs, imports the target, then RESTORES both --
deleting every stub it added and any submodules the import created -- while
keeping the freshly imported target module object (already bound by the caller).
Nothing leaks to the shared session.

A stub the target imports LAZILY at run time (custom.trigger does
``from custom.pipeline import process_pending`` inside its timer) must survive
past the import; pass its name in ``keep`` and it is re-installed after restore.
"""

import importlib
import sys
import types
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent.parent / "overlay" / "backend" / "app"


def import_isolated(target, stubs=None, extra_paths=None, keep=()):
    """Import ``target`` with ``stubs`` in place, then revert all global state.

    Args:
        target: dotted module name to import (e.g. "trail_matcher", "custom.api").
        stubs: mapping of module-name -> module object (or None to auto-create a
            bare ModuleType). A name ending a package (has a submodule) is wired
            onto its parent automatically.
        extra_paths: paths to prepend to sys.path for the import (default: the
            overlay app root).
        keep: iterable of stub names to RE-INSTALL after restore, for targets
            that import them lazily at run time.

    Returns:
        The imported target module object.
    """
    stubs = stubs or {}
    extra_paths = extra_paths or [str(OVERLAY)]
    keep = set(keep)

    path_snapshot = list(sys.path)
    # Remember which names existed (and their objects) BEFORE we touch anything,
    # so the restore is SURGICAL: we only revert what we changed. Clearing all of
    # sys.modules would break already-loaded C extensions (numpy et al.) that
    # cannot be re-initialised in the same process.
    preexisting = {name: sys.modules.get(name) for name in stubs}

    try:
        for p in reversed(extra_paths):
            sys.path.insert(0, p)

        for name, mod in stubs.items():
            if mod is None:
                mod = types.ModuleType(name)
            sys.modules[name] = mod
            if "." in name:
                parent, _, child = name.rpartition(".")
                if parent in sys.modules:
                    setattr(sys.modules[parent], child, mod)

        return importlib.import_module(target)
    finally:
        sys.path[:] = path_snapshot
        # Revert ONLY the stub names we installed: restore a pre-existing value,
        # or delete a name we introduced -- unless it is in `keep` (a stub the
        # target imports lazily at run time) or it is the target itself.
        for name, prior in preexisting.items():
            if name in keep or name == target:
                continue
            if prior is not None:
                sys.modules[name] = prior
            else:
                sys.modules.pop(name, None)
