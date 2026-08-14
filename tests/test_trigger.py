#!/usr/bin/env python3
"""Pure-logic tests for custom.trigger's debounce/coalescing behaviour.

No database:
    build/app/.venv/bin/python -m pytest tests/test_trigger.py

Stubs core.logger and custom.pipeline so only the scheduling logic is under
test. custom.trigger imports custom.pipeline LAZILY (inside its timer), so that
stub is kept past the import via ``keep=`` (see tests/_isolation.py); the rest
of the stubs are reverted so nothing leaks into the shared pytest session.
"""

import threading
import time
import types
from pathlib import Path

from _isolation import import_isolated

OVERLAY = Path(__file__).resolve().parent.parent / "overlay" / "backend" / "app"

runs = []
run_gate = threading.Event()
run_gate.set()  # not blocking by default


def _fake_process_pending(activity_id=None):
    runs.append(time.monotonic())
    run_gate.wait(timeout=5)


_core = types.ModuleType("core")
_core_logger = types.ModuleType("core.logger")
_core_logger.print_to_log = lambda *a, **k: None
_core.logger = _core_logger

_custom_pkg = types.ModuleType("custom")
_custom_pkg.__path__ = [str(OVERLAY / "custom")]

_pipeline_stub = types.ModuleType("custom.pipeline")
_pipeline_stub.process_pending = _fake_process_pending

trigger = import_isolated(
    "custom.trigger",
    stubs={
        "core": _core,
        "core.logger": _core_logger,
        "custom": _custom_pkg,
        "custom.pipeline": _pipeline_stub,
    },
    keep=("custom.pipeline",),  # trigger lazy-imports custom.pipeline at run time
)

trigger.DEBOUNCE_SECONDS = 0.15


def reset():
    runs.clear()
    run_gate.set()
    with trigger._lock:
        if trigger._timer is not None:
            trigger._timer.cancel()
        trigger._timer = None


def test_single_call_runs_once():
    reset()
    trigger.schedule_pipeline()
    time.sleep(0.5)
    assert len(runs) == 1, f"expected exactly 1 run, got {len(runs)}"


def test_burst_is_coalesced_into_one_run():
    """The whole point: a bulk import of N activities must not fire N runs."""
    reset()
    for _ in range(25):
        trigger.schedule_pipeline()
        time.sleep(0.01)  # total 0.25s > DEBOUNCE, but no gap exceeds it
    time.sleep(0.5)
    assert len(runs) == 1, f"burst should coalesce to 1 run, got {len(runs)}"


def test_separate_bursts_run_separately():
    reset()
    trigger.schedule_pipeline()
    time.sleep(0.5)
    trigger.schedule_pipeline()
    time.sleep(0.5)
    assert len(runs) == 2, f"expected 2 runs, got {len(runs)}"


def test_never_runs_concurrently():
    """activity_metadata.activity_id is UNIQUE and run_merge() issues DELETEs,
    so two overlapping process_pending() calls must be impossible."""
    reset()
    run_gate.clear()  # first run will block inside process_pending
    trigger.schedule_pipeline()
    time.sleep(0.3)
    assert len(runs) == 1, "first run should have started"

    # Ask for another run while the first is still in flight.
    trigger.schedule_pipeline()
    time.sleep(0.3)
    assert len(runs) == 1, f"must not start a concurrent run, got {len(runs)}"

    # Let the first finish; the re-armed request should then drain.
    run_gate.set()
    time.sleep(0.6)
    assert len(runs) == 2, f"re-armed request should have run, got {len(runs)}"


def test_scheduling_never_raises():
    """An import that already wrote rows must not fail on a scheduling error."""
    reset()
    orig = trigger.threading.Timer
    trigger.threading.Timer = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("cannot start thread")
    )
    try:
        trigger.schedule_pipeline()  # must swallow
    finally:
        trigger.threading.Timer = orig



