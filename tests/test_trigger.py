#!/usr/bin/env python3
"""Self-check for custom.trigger's debounce/coalescing behaviour.

Runs standalone, no framework and no database:
    python3 tests/test_trigger.py

Stubs out core.logger and custom.pipeline so only the scheduling logic is
under test.
"""

import sys
import threading
import time
import types
from pathlib import Path

OVERLAY = Path(__file__).resolve().parent.parent / "overlay" / "backend" / "app"
sys.path.insert(0, str(OVERLAY))

# --- stub core.logger -------------------------------------------------------
core = types.ModuleType("core")
core_logger = types.ModuleType("core.logger")
core_logger.print_to_log = lambda *a, **k: None
core.logger = core_logger
sys.modules["core"] = core
sys.modules["core.logger"] = core_logger

# --- stub custom package (avoid custom/__init__ pulling in SQLAlchemy) ------
custom_pkg = types.ModuleType("custom")
custom_pkg.__path__ = [str(OVERLAY / "custom")]
sys.modules["custom"] = custom_pkg

runs = []
run_gate = threading.Event()
run_gate.set()  # not blocking by default


def _fake_process_pending(activity_id=None):
    runs.append(time.monotonic())
    run_gate.wait(timeout=5)


pipeline_stub = types.ModuleType("custom.pipeline")
pipeline_stub.process_pending = _fake_process_pending
sys.modules["custom.pipeline"] = pipeline_stub

import custom.trigger as trigger  # noqa: E402

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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
