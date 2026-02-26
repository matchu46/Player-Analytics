"""
fetch_value.py — Fetch WAR, salary, and season awards for a team's players.

Data sources:
  - WAR: FanGraphs leaderboards via pybaseball
  - Salary: Spotrac team payroll page (scraped with requests + BeautifulSoup)
  - Awards: MLB Stats API (statsapi) per-player hydration

Usage:
    python src/fetch_value.py                       # fetch all (default: ARI 2025)
    python src/fetch_value.py --team LAD
    python src/fetch_value.py --season 2024
    python src/fetch_value.py --war-only
    python src/fetch_value.py --salary-only
    python src/fetch_value.py --awards-only
"""

import argparse
import os
import re
import sqlite3
import time
import unicodedata

import pandas as pd
import pybaseball as pb
import requests
import statsapi
from bs4 import BeautifulSoup

from teams import get_team, SEASON

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "db", "baseball.db")

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.spotrac.com/mlb/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Lowercase, strip accents, remove Jr/Sr/II suffixes for matching."""
    name = str(name).strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    for suffix in [" jr.", " sr.", " ii", " iii", " iv"]:
        name = name.replace(suffix, "")
    return name.strip()


# ---------------------------------------------------------------------------
# WAR from FanGraphs via pybaseball
# ---------------------------------------------------------------------------

def fetch_war(season: int, team_cfg: dict) -> pd.DataFrame:
    """Return full-season WAR for all players from FanGraphs.

    Matches by player name against the DB roster rather than filtering by team,
    so players who split time between teams get their full-season WAR totals.
    """
    fg_code = team_cfg['fg_code']

    # Load roster names from DB for matching
    roster_names = []
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        roster_names = [
            r[0] for r in conn.execute(
                "SELECT full_name FROM players WHERE season=? AND team=?", (season, fg_code)
            ).fetchall()
        ]
        conn.close()
    roster_norms = {_normalize(n): n for n in roster_names}

    print(f"Fetching FanGraphs batting stats for {season} (all teams)...")
    bat = pb.batting_stats(season, qual=1)

    print(f"Fetching FanGraphs pitching stats for {season} (all teams)...")
    pit = pb.pitching_stats(season, qual=1)

    rows = []

    # For players who split time, FanGraphs has a "- - -" / "TOT" row with combined stats.
    # Prefer TOT row if it exists; otherwise take Team=fg_code row.
    for df, pos_type in [(bat, "Batter"), (pit, "Pitcher")]:
        for name_norm, roster_name in roster_norms.items():
            # Match rows by normalized name
            mask = df["Name"].apply(_normalize) == name_norm
            matched = df[mask]
            if matched.empty:
                continue
            # Prefer multi-team combined row (Team == '- - -' or '---' or contains 'TOT')
            multi = matched[matched["Team"].str.contains(r"^-+$|TOT", na=False, regex=True)]
            row = multi.iloc[0] if not multi.empty else matched.iloc[0]
            rows.append({
                "Name": roster_name,
                "WAR": row.get("WAR"),
                "position_type": pos_type,
            })

    # Fall back: also include any team-only rows not yet matched
    team_bat = bat[bat["Team"] == fg_code].copy()
    team_pit = pit[pit["Team"] == fg_code].copy()
    already = {_normalize(r["Name"]) for r in rows}
    for df, pos_type in [(team_bat, "Batter"), (team_pit, "Pitcher")]:
        for _, row in df.iterrows():
            if _normalize(row["Name"]) not in already:
                rows.append({"Name": row["Name"], "WAR": row.get("WAR"), "position_type": pos_type})

    # Two-way players appear in both bat and pit — sum their WAR into one row
    from collections import defaultdict
    grouped: dict = defaultdict(list)
    for r in rows:
        grouped[r["Name"]].append(r)
    merged_rows = []
    for name, entries in grouped.items():
        if len(entries) == 1:
            merged_rows.append(entries[0])
        else:
            total_war = sum(e["WAR"] for e in entries if e.get("WAR") is not None)
            merged_rows.append({"Name": name, "WAR": round(total_war, 1), "position_type": "Two-Way Player"})
    rows = merged_rows

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Salary from Spotrac
# ---------------------------------------------------------------------------

def fetch_salary_spotrac(season: int, team_cfg: dict) -> pd.DataFrame:
    """Scrape Spotrac team payroll page. Returns DataFrame: Name, Salary."""
    spotrac_slug = team_cfg['spotrac_slug']
    url = f"https://www.spotrac.com/mlb/{spotrac_slug}/payroll/{season}/"
    print(f"Fetching Spotrac salary data: {url}")
    r = requests.get(url, headers=SCRAPE_HEADERS, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for table in soup.find_all("table", class_="dataTable"):
        tbody = table.find("tbody")
        if not tbody:
            continue
        for row in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 6:
                continue
            raw_name = cells[0]
            # Format: "LastName{rank}First Last" — strip leading chars up to digits
            name = re.sub(r"^.+?(\d+)", "", raw_name).strip()
            salary_str = cells[5]  # Luxury Tax AAV column
            if salary_str.startswith("$"):
                salary = int(salary_str.replace("$", "").replace(",", ""))
            else:
                salary = None
            if name and salary:
                rows.append({"Name": name, "Salary": salary})

    if not rows:
        return pd.DataFrame(columns=["Name", "Salary"])
    df = pd.DataFrame(rows).drop_duplicates(subset=["Name"])
    print(f"  Found {len(df)} salary rows from Spotrac.")
    return df


# ---------------------------------------------------------------------------
# Merge WAR + Salary and save
# ---------------------------------------------------------------------------

def fetch_war_salary(season: int, team_cfg: dict, skip_salary: bool = False):
    """Fetch WAR from FanGraphs, salary from Spotrac, merge by name, save CSV."""
    fg_code = team_cfg['fg_code']
    war_df = fetch_war(season, team_cfg)

    if not skip_salary:
        try:
            sal_df = fetch_salary_spotrac(season, team_cfg)
            # Normalize names for matching
            war_df["_norm"] = war_df["Name"].apply(_normalize)
            sal_df["_norm"] = sal_df["Name"].apply(_normalize)
            merged = war_df.merge(sal_df[["_norm", "Salary"]], on="_norm", how="left")
            merged = merged.drop(columns=["_norm"])
            # Also include salary-only players (not in FanGraphs stats — injured, etc.)
            sal_only = sal_df[~sal_df["_norm"].isin(war_df["_norm"].values)].copy()
            if not sal_only.empty:
                sal_only["WAR"] = None
                sal_only["position_type"] = None
                sal_only = sal_only.drop(columns=["_norm"])
                merged = pd.concat([merged, sal_only], ignore_index=True)
        except Exception as e:
            print(f"  WARNING: Salary scrape failed ({e}). WAR only.")
            merged = war_df.copy()
            merged["Salary"] = None
    else:
        merged = war_df.copy()
        merged["Salary"] = None

    out_path = os.path.join(RAW_DIR, f"value_{fg_code}_{season}.csv")
    merged.to_csv(out_path, index=False)
    print(f"Saved {len(merged)} rows to {out_path}")
    display_cols = [c for c in ["Name", "WAR", "Salary", "position_type"] if c in merged.columns]
    if not merged.empty and display_cols:
        print(merged[display_cols].to_string(index=False))


# ---------------------------------------------------------------------------
# Awards from MLB Stats API
# ---------------------------------------------------------------------------

def fetch_awards(season: int, team_cfg: dict):
    fg_code = team_cfg['fg_code']

    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}. Run load_db.py first.")
        return

    conn = sqlite3.connect(DB_PATH)
    player_ids = [
        r[0] for r in conn.execute(
            "SELECT player_id FROM players WHERE season=? AND team=?", (season, fg_code)
        ).fetchall()
    ]
    conn.close()

    print(f"Fetching awards for {len(player_ids)} {fg_code} players ({season})...")
    rows = []
    for pid in player_ids:
        try:
            data = statsapi.get("people", {"personIds": pid, "hydrate": "awards"})
            for person in data.get("people", []):
                for award in person.get("awards", []):
                    if str(award.get("season", "")) == str(season):
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
    out_path = os.path.join(RAW_DIR, f"awards_{fg_code}_{season}.csv")
    df.to_csv(out_path, index=False)
    print(f"Saved {len(df)} award rows to {out_path}")
    if not df.empty:
        print(df.to_string(index=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch WAR/salary/awards data")
    parser.add_argument("--team",   type=str, default="ARI", help="Team code (e.g. ARI, LAD)")
    parser.add_argument("--season", type=int, default=SEASON)
    parser.add_argument("--war-only",    action="store_true", help="Only fetch WAR (no salary scrape)")
    parser.add_argument("--salary-only", action="store_true", help="Only scrape and print salary")
    parser.add_argument("--awards-only", action="store_true", help="Only fetch awards")
    args = parser.parse_args()

    team_cfg = get_team(args.team)
    os.makedirs(RAW_DIR, exist_ok=True)

    if args.salary_only:
        df = fetch_salary_spotrac(args.season, team_cfg)
        print(df.to_string(index=False))
    elif args.awards_only:
        fetch_awards(args.season, team_cfg)
    elif args.war_only:
        fetch_war_salary(args.season, team_cfg, skip_salary=True)
    else:
        fetch_war_salary(args.season, team_cfg)
        fetch_awards(args.season, team_cfg)
