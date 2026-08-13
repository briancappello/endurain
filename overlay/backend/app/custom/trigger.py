"""Debounced background trigger for the post-processing pipeline.

Upstream's import paths create activities one at a time, and a single bulk
Garmin/Strava import can create dozens. Running the pipeline inline per
activity would issue one Overpass query per activity while the import is
still in flight, so instead every import path calls schedule_pipeline() and
bursts get coalesced into a single run on a background thread.

Nothing here is allowed to raise: an import that has already written rows to
the database must never fail because post-processing could not be scheduled.
"""

import threading

import core.logger as core_logger

# How long to wait after the last import before draining. A bulk import that
# creates N activities therefore triggers ONE pipeline run, not N.
DEBOUNCE_SECONDS = 20.0

# Guards _timer.
_lock = threading.Lock()
_timer: threading.Timer | None = None

# Held for the duration of a run. process_pending() is not safe to run
# concurrently with itself: run_merge() issues DELETEs and _ensure_metadata()
# inserts into activity_metadata, whose activity_id column is UNIQUE.
_run_lock = threading.Lock()


def schedule_pipeline():
    """Request a pipeline run, coalescing calls within DEBOUNCE_SECONDS.

    Safe (and expected) to call once per imported activity.
    """
    global _timer
    try:
        with _lock:
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(DEBOUNCE_SECONDS, _run)
            _timer.daemon = True
            _timer.start()
    except Exception as e:
        core_logger.print_to_log(
            f"Pipeline: could not schedule background run: {e}", "error"
        )


def _run():
    global _timer
    with _lock:
        _timer = None

    if not _run_lock.acquire(blocking=False):
        # A run is already in flight. It may have already passed its scan, so
        # re-arm rather than drop this request.
        schedule_pipeline()
        return

    try:
        from custom.pipeline import process_pending

        process_pending()
    except Exception as e:
        core_logger.print_to_log(f"Pipeline: background run failed: {e}", "error")
    finally:
        _run_lock.release()
