"""
update_all.py — Daily incremental pipeline for all 30 MLB teams.

Fetches the last LOOKBACK_DAYS of Statcast data for every team,
loads it into the DB (INSERT OR IGNORE skips duplicates), then
re-computes splits. Safe to run multiple times — idempotent.

Usage:
    python src/update_all.py                  # default: last 3 days
    python src/update_all.py --days 7         # last 7 days
    python src/update_all.py --team ARI       # single team only
"""

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta

# Resolve paths relative to this file so the script works from any cwd
SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(SRC, "..")

sys.path.insert(0, SRC)
from teams import TEAMS, SEASON  # noqa: E402


def run(cmd: list[str], env: dict = None) -> None:
    """Run a subprocess command, inheriting stdout/stderr."""
    full_env = {**os.environ, **(env or {})}
    subprocess.run(cmd, check=True, cwd=ROOT, env=full_env)


def update_team(team_code: str, start_date: str) -> None:
    py = sys.executable
    print(f"\n{'='*50}\nUpdating {team_code} (from {start_date})\n{'='*50}", flush=True)

    steps = [
        # Incremental Statcast fetch — only recent games
        [py, f"{SRC}/fetch.py", "--team", team_code, "--type", "statcast", "--start", start_date],
        # Load new pitches + roster into DB (INSERT OR IGNORE = no duplicates)
        [py, f"{SRC}/load_db.py", "--team", team_code, "--load"],
        # Re-compute splits so charts reflect new games
        [py, f"{SRC}/process.py", "--team", team_code, "--all"],
    ]
    for cmd in steps:
        run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily incremental update for all teams")
    parser.add_argument("--days", type=int, default=3,
                        help="Number of days back to fetch (default: 3)")
    parser.add_argument("--team", type=str, default=None,
                        help="Update a single team only (e.g. ARI)")
    args = parser.parse_args()

    start_date = (date.today() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    teams = [args.team.upper()] if args.team else list(TEAMS.keys())

    print(f"[update_all] Starting update for {len(teams)} team(s), start_date={start_date}", flush=True)

    failed = []
    for code in teams:
        try:
            update_team(code, start_date)
        except subprocess.CalledProcessError as e:
            print(f"[update_all] ERROR updating {code}: {e}", flush=True)
            failed.append(code)

    if failed:
        print(f"\n[update_all] Finished with errors: {failed}", flush=True)
        sys.exit(1)
    else:
        print(f"\n[update_all] All {len(teams)} team(s) updated successfully.", flush=True)


if __name__ == "__main__":
    main()
