"""
fetch.py — Pull Statcast + roster data for an MLB team and save to CSV.

Usage:
    python src/fetch.py --type all               # Fetch roster + Statcast (default: ARI 2025)
    python src/fetch.py --type roster            # Fetch roster only
    python src/fetch.py --type statcast          # Fetch Statcast pitches only
    python src/fetch.py --team LAD --type all    # Fetch for a different team
    python src/fetch.py --team ARI --season 2024 # Historical season
"""

import argparse
import os
import time
import pandas as pd
import statsapi
import pybaseball

from teams import get_team, SEASON, get_season_dates

pybaseball.cache.enable()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def fetch_roster(season: int, team_cfg: dict) -> pd.DataFrame:
    """Pull the full 40-man roster from MLB Stats API and return as DataFrame."""
    team_id = team_cfg['mlb_team_id']
    fg_code = team_cfg['fg_code']
    print(f"Fetching {season} {fg_code} roster...")
    raw = statsapi.get("team_roster", {"teamId": team_id, "rosterType": "40Man", "season": season})
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
    os.makedirs(RAW_DIR, exist_ok=True)
    out = os.path.join(RAW_DIR, f"roster_{fg_code}_{season}.csv")
    df.to_csv(out, index=False)
    print(f"  Saved {len(df)} players -> {out}")
    return df


# ---------------------------------------------------------------------------
# Statcast (pitch-by-pitch)
# ---------------------------------------------------------------------------

def fetch_statcast_team(season: int, team_cfg: dict, start_override: str = None) -> pd.DataFrame:
    """
    Pull all Statcast pitches involving a team's players:

    - Pitching side: statcast(team=fg_code) returns every pitch thrown BY a
      team pitcher.

    - Batting side: statcast_batter() per position player returns every pitch
      FACED BY that batter. There is NO overlap with the pitching pull because
      a pitch can only be thrown by one team per plate appearance.

    Both halves are combined and saved to statcast_{team}_{season}.csv.
    Pass start_override to fetch only a recent window (e.g. for daily updates).
    """
    fg_code = team_cfg['fg_code']
    from datetime import date as _date
    dates = get_season_dates(season)
    start = start_override or dates['season_start']
    # Cap end to today for incremental fetches — querying future dates returns malformed data
    season_end = dates['season_end']
    today = _date.today().strftime("%Y-%m-%d")
    end = min(season_end, today) if start_override else season_end

    roster_path = os.path.join(RAW_DIR, f"roster_{fg_code}_{season}.csv")
    if not os.path.exists(roster_path):
        print(f"Roster file not found. Fetching roster first...")
        fetch_roster(season, team_cfg)
    roster = pd.read_csv(roster_path)

    # --- Pitching side ---
    print(f"\nFetching Statcast — {fg_code} pitching ({start} to {end})...")
    team_pitching = pybaseball.statcast(start_dt=start, end_dt=end, team=fg_code)
    team_pitching = team_pitching.copy()
    team_pitching["data_side"] = "pitching"
    print(f"  Got {len(team_pitching):,} pitches thrown by {fg_code} pitchers.")
    time.sleep(3)

    # --- Batting side: per-batter pulls ---
    batters = roster[roster["position_type"] != "Pitcher"].dropna(subset=["player_id"])
    print(f"\nFetching Statcast — {fg_code} batting ({len(batters)} batters)...")

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
        team_batting = pd.concat(batting_frames, ignore_index=True)
        print(f"  Total {fg_code} batting pitches: {len(team_batting):,}")
    else:
        print("  WARNING: No batting data retrieved.")
        team_batting = pd.DataFrame()

    # --- Combine ---
    print("\nCombining pitching + batting data...")
    combined = pd.concat([team_pitching, team_batting], ignore_index=True)

    # Safety dedup in case any pitch somehow appears in both pulls
    before = len(combined)
    combined = combined.dropna(subset=["game_pk", "at_bat_number", "pitch_number"])
    combined = combined.drop_duplicates(subset=["game_pk", "at_bat_number", "pitch_number"])
    if len(combined) < before:
        print(f"  Removed {before - len(combined):,} duplicate rows.")

    os.makedirs(RAW_DIR, exist_ok=True)
    out = os.path.join(RAW_DIR, f"statcast_{fg_code}_{season}.csv")
    combined.to_csv(out, index=False)
    print(f"  Saved {len(combined):,} total pitches -> {out}")
    return combined


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch MLB team Statcast + roster data")
    parser.add_argument("--type", choices=["all", "roster", "statcast"],
                        default="roster", help="What to fetch")
    parser.add_argument("--team",   type=str, default="ARI", help="Team code (e.g. ARI, LAD)")
    parser.add_argument("--season", type=int, default=SEASON)
    parser.add_argument("--start",  type=str, default=None,
                        help="Override Statcast start date (YYYY-MM-DD). Useful for daily incremental updates.")
    args = parser.parse_args()

    team_cfg = get_team(args.team)

    if args.type in ("all", "roster"):
        fetch_roster(args.season, team_cfg)

    if args.type in ("all", "statcast"):
        fetch_statcast_team(args.season, team_cfg, start_override=args.start)
