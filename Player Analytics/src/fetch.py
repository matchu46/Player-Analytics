"""
fetch.py — Pull 2025 D-backs Statcast + roster data and save to CSV.

Usage:
    python src/fetch.py --type all        # Fetch roster + all Statcast data
    python src/fetch.py --type roster     # Fetch roster only
    python src/fetch.py --type statcast   # Fetch Statcast pitches only
"""

import argparse
import os
import time
import pandas as pd
import statsapi
import pybaseball

pybaseball.cache.enable()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")

ARI_TEAM_ID = 109
ARI_CODE = "ARI"
SEASON_START = "2025-03-27"
SEASON_END = "2025-10-01"


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def fetch_roster(season: int = 2025) -> pd.DataFrame:
    """Pull the full 40-man roster from MLB Stats API and return as DataFrame."""
    print(f"Fetching {season} D-backs roster...")
    raw = statsapi.get("team_roster", {"teamId": ARI_TEAM_ID, "rosterType": "40Man", "season": season})
    rows = []
    for entry in raw.get("roster", []):
        person = entry.get("person", {})
        position = entry.get("position", {})
        rows.append({
            "player_id": person.get("id"),
            "full_name": person.get("fullName"),
            "jersey_number": entry.get("jerseyNumber"),
            "position": position.get("abbreviation"),
            "position_type": position.get("type"),   # Pitcher / Hitter / etc.
            "status": entry.get("status", {}).get("description"),
        })
    df = pd.DataFrame(rows)
    out = os.path.join(RAW_DIR, f"roster_{season}.csv")
    df.to_csv(out, index=False)
    print(f"  Saved {len(df)} players -> {out}")
    return df


# ---------------------------------------------------------------------------
# Statcast (pitch-by-pitch)
# ---------------------------------------------------------------------------

def fetch_statcast_team(start: str = SEASON_START, end: str = SEASON_END,
                        season: int = 2025) -> pd.DataFrame:
    """
    Pull all Statcast pitches involving ARI players:

    - Pitching side: pybaseball.statcast(team='ARI') returns every pitch
      thrown BY an ARI pitcher (player_type=pitcher internally).

    - Batting side: statcast_batter() per ARI position player returns every
      pitch FACED BY that batter. There is NO overlap with the pitching pull
      because a pitch can only be thrown by one team per plate appearance.

    Both halves are combined and saved to statcast_{season}.csv.
    """
    roster_path = os.path.join(RAW_DIR, f"roster_{season}.csv")
    if not os.path.exists(roster_path):
        print(f"Roster file not found. Fetching roster first...")
        fetch_roster(season)
    roster = pd.read_csv(roster_path)

    # --- Pitching side ---
    print(f"\nFetching Statcast — ARI pitching ({start} to {end})...")
    ari_pitching = pybaseball.statcast(start_dt=start, end_dt=end, team=ARI_CODE)
    ari_pitching = ari_pitching.copy()
    ari_pitching["data_side"] = "pitching"
    print(f"  Got {len(ari_pitching):,} pitches thrown by ARI pitchers.")
    time.sleep(3)

    # --- Batting side: per-batter pulls ---
    batters = roster[roster["position_type"] != "Pitcher"].dropna(subset=["player_id"])
    print(f"\nFetching Statcast — ARI batting ({len(batters)} batters)...")

    batting_frames = []
    for _, row in batters.iterrows():
        pid = int(row["player_id"])
        name = row["full_name"]
        try:
            df = pybaseball.statcast_batter(start, end, player_id=pid)
            if df is not None and not df.empty:
                df = df.copy()
                df["data_side"] = "batting"
                batting_frames.append(df)
                print(f"  {name}: {len(df):,} pitches")
            else:
                print(f"  {name}: no data")
            time.sleep(1)
        except Exception as e:
            print(f"  {name}: ERROR — {e}")
            continue

    if batting_frames:
        ari_batting = pd.concat(batting_frames, ignore_index=True)
        print(f"  Total ARI batting pitches: {len(ari_batting):,}")
    else:
        print("  WARNING: No batting data retrieved.")
        ari_batting = pd.DataFrame()

    # --- Combine ---
    print("\nCombining pitching + batting data...")
    combined = pd.concat([ari_pitching, ari_batting], ignore_index=True)

    # Safety dedup in case any pitch somehow appears in both pulls
    before = len(combined)
    combined = combined.dropna(subset=["game_pk", "at_bat_number", "pitch_number"])
    combined = combined.drop_duplicates(subset=["game_pk", "at_bat_number", "pitch_number"])
    if len(combined) < before:
        print(f"  Removed {before - len(combined):,} duplicate rows.")

    out = os.path.join(RAW_DIR, f"statcast_{season}.csv")
    combined.to_csv(out, index=False)
    print(f"  Saved {len(combined):,} total pitches -> {out}")
    return combined


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch D-backs 2025 data")
    parser.add_argument("--type", choices=["all", "roster", "statcast"],
                        default="roster", help="What to fetch")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()

    if args.type in ("all", "roster"):
        fetch_roster(args.season)

    if args.type in ("all", "statcast"):
        fetch_statcast_team(season=args.season)
