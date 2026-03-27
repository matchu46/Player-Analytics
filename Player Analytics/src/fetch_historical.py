"""
fetch_historical.py — Bulk fetch + load + process historical Statcast data.

Loops over every (season, team) combination, running the full pipeline for each.
Uses a JSON checkpoint file so the run can be safely interrupted and resumed.

Usage:
    # Fetch all teams, all seasons (2016-2024)
    python src/fetch_historical.py

    # Specific seasons only
    python src/fetch_historical.py --seasons 2016 2017 2018

    # Specific teams only
    python src/fetch_historical.py --teams ARI LAD NYY

    # Show what would run without doing anything
    python src/fetch_historical.py --dry-run

    # Reset a specific job so it re-runs
    python src/fetch_historical.py --reset ARI 2019

    # Show current checkpoint status
    python src/fetch_historical.py --status

Checkpoint file: data/historical_checkpoint.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.join(SRC_DIR, "..")
DATA_DIR = os.path.join(ROOT_DIR, "data")
CHECKPOINT_PATH = os.path.join(DATA_DIR, "historical_checkpoint.json")

sys.path.insert(0, SRC_DIR)
from teams import TEAMS, SEASON_DATES

# Historical seasons (excludes current/live seasons handled by update_all.py)
HISTORICAL_SEASONS = sorted(s for s in SEASON_DATES if s <= 2024)

ALL_TEAMS = sorted(TEAMS.keys())
PY = sys.executable


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {"done": [], "failed": {}}


def save_checkpoint(cp: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(cp, f, indent=2)


def cp_key(team: str, season: int) -> str:
    return f"{team}_{season}"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def csv_exists(team_code: str, season: int) -> bool:
    fg_code = TEAMS[team_code]['fg_code']
    path = os.path.join(DATA_DIR, "raw", f"statcast_{fg_code}_{season}.csv")
    return os.path.exists(path)


def run_step(cmd: list, label: str) -> tuple[bool, str]:
    """Run a subprocess command. Returns (success, error)."""
    short = " ".join(os.path.basename(c) if c.endswith(".py") else c for c in cmd)
    print(f"  [{label}] {short}", flush=True)
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0:
        err = f"{label} exited with code {result.returncode}"
        print(f"  FAILED: {err}", flush=True)
        return False, err
    return True, ""


def run_pipeline(team: str, season: int) -> tuple[bool, str]:
    """Run fetch -> load -> process for one (team, season)."""
    steps = []

    if csv_exists(team, season):
        print(f"  Statcast CSV already exists — skipping fetch.", flush=True)
    else:
        steps.append(([PY, f"{SRC_DIR}/fetch.py",
                       "--team", team, "--season", str(season), "--type", "all"],
                      "fetch"))

    steps += [
        ([PY, f"{SRC_DIR}/load_db.py",
          "--team", team, "--season", str(season), "--load"], "load"),
        ([PY, f"{SRC_DIR}/process.py",
          "--team", team, "--season", str(season), "--all"],  "process"),
    ]

    for cmd, label in steps:
        ok, err = run_step(cmd, label)
        if not ok:
            return False, err
        time.sleep(2)

    return True, ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bulk fetch historical Statcast data (2016-2024)")
    parser.add_argument("--seasons", type=int, nargs="+", default=HISTORICAL_SEASONS)
    parser.add_argument("--teams",   type=str, nargs="+", default=ALL_TEAMS)
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    parser.add_argument("--status",  action="store_true", help="Show checkpoint status and exit")
    parser.add_argument("--reset",   type=str, nargs=2, metavar=("TEAM", "SEASON"),
                        help="Remove TEAM SEASON from checkpoint so it re-runs")
    args = parser.parse_args()

    args.teams = [t.upper() for t in args.teams]
    invalid = [t for t in args.teams if t not in TEAMS]
    if invalid:
        print(f"Unknown team codes: {invalid}")
        sys.exit(1)
    invalid_s = [s for s in args.seasons if s not in SEASON_DATES]
    if invalid_s:
        print(f"No SEASON_DATES configured for: {invalid_s}")
        sys.exit(1)

    cp = load_checkpoint()

    # --status
    if args.status:
        all_keys = [cp_key(t, s) for s in sorted(args.seasons) for t in args.teams]
        done    = [k for k in all_keys if k in cp["done"]]
        failed  = [k for k in all_keys if k in cp["failed"]]
        pending = [k for k in all_keys if k not in cp["done"] and k not in cp["failed"]]
        print(f"Status for {len(all_keys)} jobs:")
        print(f"  Done   : {len(done)}")
        print(f"  Failed : {len(failed)}")
        print(f"  Pending: {len(pending)}")
        if failed:
            print("\nFailed jobs:")
            for k in failed:
                print(f"  {k}: {cp['failed'][k]}")
        return

    # --reset
    if args.reset:
        k = cp_key(args.reset[0].upper(), int(args.reset[1]))
        removed = False
        if k in cp["done"]:
            cp["done"].remove(k)
            removed = True
        if k in cp["failed"]:
            del cp["failed"][k]
            removed = True
        save_checkpoint(cp)
        print(f"{'Reset' if removed else 'Not found in checkpoint'}: {k}")
        return

    # Build work list (pending only — not done, not failed from a previous run)
    # Failed jobs ARE retried on next run (they're not in "done")
    work = [(t, s) for s in sorted(args.seasons)
                   for t in args.teams
                   if cp_key(t, s) not in cp["done"]]

    already_done = sum(1 for s in args.seasons for t in args.teams
                       if cp_key(t, s) in cp["done"])
    total = len(work)

    print(f"\nHistorical fetch plan:")
    print(f"  Seasons : {sorted(args.seasons)}")
    print(f"  Teams   : {len(args.teams)}")
    print(f"  Done    : {already_done} / {already_done + total}")
    print(f"  To run  : {total} jobs")
    if cp["failed"]:
        retry = [k for k in cp["failed"] if any(cp_key(t,s)==k for t,s in work)]
        print(f"  Retrying: {len(retry)} previously failed jobs")

    if args.dry_run:
        print("\nJobs that would run:")
        for t, s in work:
            marker = " (retry)" if cp_key(t,s) in cp["failed"] else ""
            print(f"  {t} {s}{marker}")
        return

    if total == 0:
        print("\nAll jobs already completed. Use --reset TEAM SEASON to re-run one.")
        return

    print(f"\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Checkpoint saves after each job. Ctrl+C is safe — resume by re-running.\n")

    completed = 0
    for i, (team, season) in enumerate(work, 1):
        k = cp_key(team, season)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{i}/{total}] {team} {season}  ({ts})", flush=True)

        ok, err = run_pipeline(team, season)

        if ok:
            cp["done"].append(k)
            cp["failed"].pop(k, None)
            completed += 1
        else:
            cp["failed"][k] = err
            print(f"  Job failed, continuing. Will retry on next run.", flush=True)

        save_checkpoint(cp)

        if i < total:
            print(f"  Sleeping 15s before next job...", flush=True)
            time.sleep(15)

    # Final summary
    print(f"\n{'='*60}")
    print(f"Completed: {completed}/{total} jobs this run")
    if cp["failed"]:
        still_failed = {k: v for k, v in cp["failed"].items()
                        if any(cp_key(t, s) == k for t, s in work)}
        if still_failed:
            print(f"\nFailed ({len(still_failed)}) — re-run this script to retry:")
            for k, err in still_failed.items():
                print(f"  {k}: {err}")

    all_done = sum(1 for s in HISTORICAL_SEASONS for t in ALL_TEAMS
                   if cp_key(t, s) in cp["done"])
    all_total = len(HISTORICAL_SEASONS) * len(ALL_TEAMS)
    print(f"\nOverall progress: {all_done}/{all_total} historical jobs done")

    if all_done == all_total:
        print("\nAll historical data fetched! Next steps:")
        print("  1. Compress DB:")
        print("     python -c \"import gzip, shutil; shutil.copyfileobj(open('data/db/baseball.db','rb'), gzip.open('data/db/baseball.db.gz','wb',compresslevel=6))\"")
        print("  2. Upload data/db/baseball.db.gz to GitHub Releases as a new release asset")
        print("  3. Bump DB_VERSION in Railway Variables to force re-seed on next deploy")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
