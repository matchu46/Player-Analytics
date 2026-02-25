"""
run_pipeline.py — One-shot script to run the full data pipeline.

Steps:
    1. Fetch 2025 D-backs roster
    2. Fetch Statcast pitch-by-pitch data
    3. Create SQLite database schema
    4. Load data into DB
    5. Compute all situational splits

Usage:
    python run_pipeline.py              # Full pipeline
    python run_pipeline.py --step 1    # Roster only
    python run_pipeline.py --step 3    # Create DB schema only
    python run_pipeline.py --season 2026  # Different season
"""

import argparse
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from fetch import fetch_roster, fetch_statcast_team
from load_db import create_db, load_roster, load_statcast
from process import process_batters, process_pitchers


def run_pipeline(season: int = 2025, start_step: int = 1):
    print("=" * 60)
    print(f"D-backs Analytics Pipeline — {season} Season")
    print("=" * 60)

    if start_step <= 1:
        print("\n[Step 1/5] Fetching roster...")
        fetch_roster(season)

    if start_step <= 2:
        print("\n[Step 2/5] Fetching Statcast pitch data (this may take a while)...")
        fetch_statcast_team()

    if start_step <= 3:
        print("\n[Step 3/5] Creating database schema...")
        create_db()

    if start_step <= 4:
        print("\n[Step 4/5] Loading data into database...")
        load_roster(season)
        load_statcast(season)

    if start_step <= 5:
        print("\n[Step 5/5] Computing situational splits...")
        process_batters(season=season)
        process_pitchers(season=season)

    print("\n" + "=" * 60)
    print("Pipeline complete!")
    print("Run: python app/app.py  to start the web server.")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=1,
                        help="Start from this step (1-5). Useful for resuming after failure.")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()
    run_pipeline(season=args.season, start_step=args.step)
