"""
fetch_historical.py — Backfill pipeline for historical seasons (2022–2024).

Runs roster + Statcast fetch, DB load, and splits processing for every team
for each specified season. Skips a team/season if the CSV already exists
(so it's safe to resume after interruption).

Usage:
    python src/fetch_historical.py                        # 2022, 2023, 2024 all teams
    python src/fetch_historical.py --seasons 2024         # single season
    python src/fetch_historical.py --team ARI             # single team all seasons
    python src/fetch_historical.py --seasons 2023 2024 --team LAD
"""

import argparse
import os
import subprocess
import sys

SRC  = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(SRC, "..")

sys.path.insert(0, SRC)
from teams import TEAMS, SEASON_DATES  # noqa: E402

HISTORICAL_SEASONS = [2022, 2023, 2024]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, cwd=ROOT)


def already_fetched(team_code: str, season: int) -> bool:
    """Return True if the Statcast CSV for this team/season already exists."""
    cfg = TEAMS[team_code]
    path = os.path.join(ROOT, "data", "raw", f"statcast_{cfg['fg_code']}_{season}.csv")
    return os.path.exists(path)


def process_team_season(team_code: str, season: int) -> None:
    py = sys.executable
    print(f"\n{'='*55}\n{team_code} {season}\n{'='*55}", flush=True)

    if already_fetched(team_code, season):
        print(f"  Statcast CSV already exists — skipping fetch, re-loading/processing.")
        steps = [
            [py, f"{SRC}/load_db.py",  "--team", team_code, "--season", str(season), "--all"],
            [py, f"{SRC}/process.py",  "--team", team_code, "--season", str(season), "--all"],
        ]
    else:
        steps = [
            [py, f"{SRC}/fetch.py",    "--team", team_code, "--season", str(season), "--type", "all"],
            [py, f"{SRC}/load_db.py",  "--team", team_code, "--season", str(season), "--all"],
            [py, f"{SRC}/process.py",  "--team", team_code, "--season", str(season), "--all"],
        ]

    for cmd in steps:
        run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical data backfill")
    parser.add_argument("--seasons", nargs="+", type=int, default=HISTORICAL_SEASONS,
                        help="Seasons to backfill (default: 2022 2023 2024)")
    parser.add_argument("--team", type=str, default=None,
                        help="Single team code (default: all 30 teams)")
    args = parser.parse_args()

    teams   = [args.team.upper()] if args.team else list(TEAMS.keys())
    seasons = sorted(args.seasons)

    total = len(teams) * len(seasons)
    print(f"[fetch_historical] {len(seasons)} season(s) × {len(teams)} team(s) = {total} jobs", flush=True)

    failed = []
    for season in seasons:
        for code in teams:
            try:
                process_team_season(code, season)
            except subprocess.CalledProcessError as e:
                print(f"[fetch_historical] ERROR {code} {season}: {e}", flush=True)
                failed.append((code, season))

    if failed:
        print(f"\n[fetch_historical] Finished with errors: {failed}", flush=True)
        sys.exit(1)
    else:
        print(f"\n[fetch_historical] All {total} jobs complete.", flush=True)
        print("Next steps:", flush=True)
        print("  1. Compress:  python -c \"import gzip,shutil; [shutil.copyfileobj(open('data/db/baseball.db','rb'), gzip.open('data/db/baseball.db.gz','wb',compresslevel=6))]\"", flush=True)
        print("  2. Push:      git add data/db/baseball.db.gz && git commit -m 'Add 2022-2024 historical data' && git push", flush=True)
        print("  3. Railway:   bump DB_VERSION env var to force re-seed", flush=True)


if __name__ == "__main__":
    main()
