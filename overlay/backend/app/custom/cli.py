"""CLI entry point for custom Endurain operations.

Usage (from /opt/endurain/app):
    .venv/bin/python -m custom.cli sync [--days N]
    .venv/bin/python -m custom.cli process [--id ACTIVITY_ID]
    .venv/bin/python -m custom.cli match --id ACTIVITY_ID [--update]
"""

import argparse
import asyncio
import os
import sys
import warnings

# Silence stravalib token warnings (normally set in main.py which the CLI bypasses)
os.environ["SILENCE_TOKEN_WARNINGS"] = "TRUE"

# Suppress psycopg ResourceWarning for unclosed connections from upstream code
warnings.filterwarnings("ignore", category=ResourceWarning, module="psycopg")

# PG18 compat must be imported before any database access
import pg18_compat  # noqa: F401


def cmd_sync(args):
    """Fetch new activities from Garmin Connect and Strava, then post-process."""
    import strava.utils as strava_utils
    import strava.activity_utils as strava_activity_utils
    import garmin.activity_utils as garmin_activity_utils
    import core.logger as core_logger
    from custom.pipeline import process_pending

    core_logger.setup_main_logger()
    days = args.days

    print(f"Syncing last {days} day(s) of activities...")

    # Refresh Strava tokens
    print("Refreshing Strava tokens...")
    strava_utils.refresh_strava_tokens(True)

    # Fetch from Garmin
    print("Fetching from Garmin Connect...")
    asyncio.run(
        garmin_activity_utils.retrieve_garminconnect_users_activities_for_days(days)
    )

    # Fetch from Strava
    print("Fetching from Strava...")
    asyncio.run(
        strava_activity_utils.retrieve_strava_users_activities_for_days(days, True)
    )

    # Run post-processing pipeline
    print("Running post-processing pipeline...")
    process_pending()

    print("Done.")


def cmd_process(args):
    """Run post-processing pipeline on pending activities."""
    import core.logger as core_logger
    from custom.pipeline import process_pending

    core_logger.setup_main_logger()

    if args.id:
        print(f"Processing activity {args.id}...")
        process_pending(activity_id=args.id)
    else:
        print("Processing all pending activities...")
        process_pending()

    print("Done.")


def cmd_match(args):
    """Run trail matching on a specific activity (delegates to trail_matcher CLI)."""
    from trail_matcher import match_trails_for_activity, generate_trail_name
    from core.database import SessionLocal
    import sqlalchemy

    matches = match_trails_for_activity(args.id)

    if not matches:
        print(f"No trail matches found for activity {args.id}")
        return

    print(f"\nTrail matches for activity {args.id}:")
    print(
        f"{'Trail Name':40s} {'Type':10s} {'Points':>8s} {'Fraction':>10s} {'Avg Dist':>10s}"
    )
    print("-" * 82)
    for m in matches:
        print(
            f"{m['name']:40s} {m['highway']:10s} "
            f"{m['points_near']:>5d}/{m['points_total']:<5d} "
            f"{m['fraction']:>8.1%} "
            f"{m['avg_distance_m']:>8.1f}m"
        )

    db = SessionLocal()
    row = db.execute(
        sqlalchemy.text("SELECT activity_type, name FROM activities WHERE id = :aid"),
        {"aid": args.id},
    ).fetchone()
    db.close()

    if row:
        suggested = generate_trail_name(matches, activity_type=row[0])
        print(f"\nCurrent name:   {row[1]}")
        print(f"Suggested name: {suggested}")

        if args.update and suggested:
            db = SessionLocal()
            db.execute(
                sqlalchemy.text("UPDATE activities SET name = :name WHERE id = :aid"),
                {"name": suggested, "aid": args.id},
            )
            db.commit()
            db.close()
            print(f"Updated activity {args.id} name to: {suggested}")


def cmd_merge(args):
    """Run merge detection only (no trail matching)."""
    import core.logger as core_logger
    from custom.merge import run_merge

    core_logger.setup_main_logger()

    print("Running merge detection...")
    run_merge()
    print("Done.")


def cmd_segments(args):
    """View segment data and personal leaderboards."""
    from core.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()

    if args.activity:
        # Show segments for a specific activity
        rows = db.execute(
            text("""
            SELECT s.name, se.elapsed_time, se.moving_time,
                   se.pr_rank, se.kom_rank,
                   se.average_heartrate, se.average_watts,
                   s.distance, s.average_grade, s.climb_category,
                   s.strava_id
            FROM segment_efforts se
            JOIN segments s ON s.id = se.segment_id
            WHERE se.activity_id = :aid
            ORDER BY se.start_index
        """),
            {"aid": args.activity},
        ).fetchall()

        if not rows:
            print(f"No segment efforts for activity {args.activity}")
            db.close()
            return

        print(f"\nSegment efforts for activity {args.activity}:")
        print(
            f"{'Segment':40s} {'Time':>7s} {'PR':>3s} {'KOM':>4s} {'HR':>5s} {'W':>5s} {'Dist':>6s} {'Grade':>6s}"
        )
        print("-" * 85)
        for r in rows:
            name = r[0][:39]
            elapsed = f"{r[1] // 60}:{r[1] % 60:02d}" if r[1] else "—"
            pr = str(r[3]) if r[3] else "—"
            kom = str(r[4]) if r[4] else "—"
            hr = f"{r[5]:.0f}" if r[5] else "—"
            watts = f"{r[6]:.0f}" if r[6] else "—"
            dist = f"{r[7] / 1000:.1f}k" if r[7] else "—"
            grade = f"{r[8]:.1f}%" if r[8] is not None else "—"
            print(
                f"{name:40s} {elapsed:>7s} {pr:>3s} {kom:>4s} {hr:>5s} {watts:>5s} {dist:>6s} {grade:>6s}"
            )

    elif args.id:
        # Show personal leaderboard for a specific segment
        seg = db.execute(
            text("""
            SELECT s.name, s.distance, s.average_grade, s.climb_category,
                   s.city, s.state, s.elevation_high, s.elevation_low,
                   s.effort_count, s.athlete_count
            FROM segments s WHERE s.id = :sid
        """),
            {"sid": args.id},
        ).fetchone()

        if not seg:
            print(f"Segment {args.id} not found")
            db.close()
            return

        cat_names = {0: "NC", 1: "4", 2: "3", 3: "2", 4: "1", 5: "HC"}
        cat = cat_names.get(seg[3], "?")
        print(f"\n{seg[0]}")
        print(f"  {seg[4] or ''}, {seg[5] or ''}")
        print(f"  Distance: {seg[1] / 1000:.2f} km | Grade: {seg[2]:.1f}% | Cat: {cat}")
        print(f"  Elevation: {seg[7]:.0f} - {seg[6]:.0f} m")
        if seg[8]:
            print(f"  Global: {seg[8]} efforts, {seg[9]} athletes")

        efforts = db.execute(
            text("""
            SELECT se.elapsed_time, se.moving_time, se.start_date,
                   se.average_heartrate, se.average_watts, se.pr_rank,
                   a.name as activity_name, se.activity_id
            FROM segment_efforts se
            JOIN activities a ON a.id = se.activity_id
            WHERE se.segment_id = :sid
            ORDER BY se.elapsed_time ASC
        """),
            {"sid": args.id},
        ).fetchall()

        if efforts:
            print(f"\n  Personal leaderboard ({len(efforts)} efforts):")
            print(
                f"  {'#':>3s}  {'Time':>7s}  {'PR':>3s}  {'HR':>5s}  {'W':>5s}  {'Date':10s}  Activity"
            )
            print(f"  {'-' * 70}")
            for i, e in enumerate(efforts, 1):
                elapsed = f"{e[0] // 60}:{e[0] % 60:02d}"
                pr = str(e[5]) if e[5] else "—"
                hr = f"{e[3]:.0f}" if e[3] else "—"
                watts = f"{e[4]:.0f}" if e[4] else "—"
                date = e[2].strftime("%Y-%m-%d") if e[2] else "—"
                print(
                    f"  {i:3d}  {elapsed:>7s}  {pr:>3s}  {hr:>5s}  {watts:>5s}  {date}  {e[6]}"
                )
        else:
            print("\n  No efforts recorded")

    else:
        # List all segments with effort counts
        rows = db.execute(
            text("""
            SELECT s.id, s.name, s.distance, s.average_grade, s.climb_category,
                   COUNT(se.id) as effort_count,
                   MIN(se.elapsed_time) as best_time,
                   s.city, s.state
            FROM segments s
            LEFT JOIN segment_efforts se ON se.segment_id = s.id
            GROUP BY s.id
            ORDER BY effort_count DESC, s.name
        """)
        ).fetchall()

        if not rows:
            print("No segments found")
            db.close()
            return

        print(
            f"\n{'ID':>4s}  {'Segment':40s} {'Efforts':>7s} {'Best':>7s} {'Dist':>6s} {'Grade':>6s}  Location"
        )
        print("-" * 100)
        for r in rows:
            best = f"{r[6] // 60}:{r[6] % 60:02d}" if r[6] else "—"
            dist = f"{r[2] / 1000:.1f}k" if r[2] else "—"
            grade = f"{r[3]:.1f}%" if r[3] is not None else "—"
            loc = f"{r[7] or ''}, {r[8] or ''}".strip(", ")
            print(
                f"{r[0]:4d}  {r[1]:40s} {r[5]:>7d} {best:>7s} {dist:>6s} {grade:>6s}  {loc}"
            )

    db.close()


def main():
    parser = argparse.ArgumentParser(
        prog="python -m custom.cli",
        description="Custom Endurain CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = sub.add_parser("sync", help="Fetch from Garmin/Strava and post-process")
    p_sync.add_argument(
        "--days", type=int, default=1, help="Days to look back (default: 1)"
    )
    p_sync.set_defaults(func=cmd_sync)

    # process
    p_proc = sub.add_parser("process", help="Run post-processing pipeline")
    p_proc.add_argument("--id", type=int, help="Process a specific activity ID")
    p_proc.set_defaults(func=cmd_process)

    # merge
    p_merge = sub.add_parser("merge", help="Run merge detection only")
    p_merge.set_defaults(func=cmd_merge)

    # segments
    p_seg = sub.add_parser("segments", help="View segments and personal leaderboards")
    p_seg.add_argument(
        "--id", type=int, help="Show leaderboard for a specific segment ID"
    )
    p_seg.add_argument(
        "--activity", type=int, help="Show segments for a specific activity"
    )
    p_seg.set_defaults(func=cmd_segments)

    # match
    p_match = sub.add_parser("match", help="Trail-match a specific activity")
    p_match.add_argument("--id", type=int, required=True, help="Activity ID")
    p_match.add_argument(
        "--update", action="store_true", help="Update the activity name"
    )
    p_match.set_defaults(func=cmd_match)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
