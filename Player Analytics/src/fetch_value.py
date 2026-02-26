"""
fetch_value.py — Fetch WAR, salary, and season awards for D-backs players.

Data sources:
  - WAR + Salary: FanGraphs leaderboards via pybaseball
  - Awards: MLB Stats API (statsapi) per-player hydration

Usage:
    python src/fetch_value.py              # fetch both (default season 2025)
    python src/fetch_value.py --season 2024
    python src/fetch_value.py --war-only
    python src/fetch_value.py --awards-only
"""

import argparse
import os
import sqlite3
import time

import pandas as pd
import pybaseball as pb
import statsapi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")

FG_TEAM = "ARI"  # FanGraphs team abbreviation (NOT 'AZ' — that's Statcast only)


# ---------------------------------------------------------------------------
# WAR + Salary from FanGraphs via pybaseball
# ---------------------------------------------------------------------------

def fetch_war_salary(season: int = 2025):
    print(f"Fetching FanGraphs batting stats for {season}...")
    bat = pb.batting_stats(season, qual=1)
    bat_az = bat[bat["Team"] == FG_TEAM][["Name", "WAR"]].copy()
    if "Salary" in bat.columns:
        bat_az["Salary"] = bat[bat["Team"] == FG_TEAM]["Salary"].values
    else:
        bat_az["Salary"] = None
    bat_az["position_type"] = "Batter"

    print(f"Fetching FanGraphs pitching stats for {season}...")
    pit = pb.pitching_stats(season, qual=1)
    pit_az = pit[pit["Team"] == FG_TEAM][["Name", "WAR"]].copy()
    if "Salary" in pit.columns:
        pit_az["Salary"] = pit[pit["Team"] == FG_TEAM]["Salary"].values
    else:
        pit_az["Salary"] = None
    pit_az["position_type"] = "Pitcher"

    combined = pd.concat([bat_az, pit_az], ignore_index=True)
    out_path = os.path.join(RAW_DIR, f"value_{season}.csv")
    combined.to_csv(out_path, index=False)
    print(f"Saved {len(combined)} rows to {out_path}")
    print(combined[["Name", "WAR", "Salary", "position_type"]].to_string(index=False))


# ---------------------------------------------------------------------------
# Awards from MLB Stats API
# ---------------------------------------------------------------------------

AWARD_NAMES = {
    # Map award IDs to clean display names where statsapi returns raw names
}

def fetch_awards(season: int = 2025):
    # Load player IDs from DB
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}. Run load_db.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    player_ids = [
        r[0] for r in conn.execute(
            "SELECT player_id FROM players WHERE season=?", (season,)
        ).fetchall()
    ]
    conn.close()

    print(f"Fetching awards for {len(player_ids)} players ({season})...")
    rows = []
    for pid in player_ids:
        try:
            data = statsapi.get("people", {"personIds": pid, "hydrate": "awards"})
            for person in data.get("people", []):
                for award in person.get("awards", []):
                    award_season = str(award.get("season", ""))
                    if award_season == str(season):
                        rows.append({
                            "player_id": pid,
                            "season": season,
                            "award_name": award.get("name", "Unknown Award"),
                        })
        except Exception as e:
            print(f"  Error fetching awards for player {pid}: {e}")
        time.sleep(0.3)

    df = pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["player_id", "season", "award_name"]
    )
    out_path = os.path.join(RAW_DIR, f"awards_{season}.csv")
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} award rows to {out_path}")
    if not df.empty:
        print(df.to_string(index=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch WAR/salary/awards data")
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument("--war-only", action="store_true", help="Only fetch WAR/salary")
    parser.add_argument("--awards-only", action="store_true", help="Only fetch awards")
    args = parser.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)

    if not args.awards_only:
        fetch_war_salary(args.season)

    if not args.war_only:
        fetch_awards(args.season)
