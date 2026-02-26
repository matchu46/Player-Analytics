"""
fetch_value.py — Fetch WAR, salary, and season awards for D-backs players.

Data sources:
  - WAR: FanGraphs leaderboards via pybaseball
  - Salary: Spotrac team payroll page (scraped with requests + BeautifulSoup)
  - Awards: MLB Stats API (statsapi) per-player hydration

Usage:
    python src/fetch_value.py              # fetch all (default season 2025)
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")

FG_TEAM = "ARI"       # FanGraphs team abbreviation (NOT 'AZ' — that's Statcast only)
SPOTRAC_SLUG = "arizona-diamondbacks"

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

def fetch_war(season: int = 2025) -> pd.DataFrame:
    """Return DataFrame with columns: Name, WAR, position_type for D-backs players."""
    print(f"Fetching FanGraphs batting stats for {season}...")
    bat = pb.batting_stats(season, qual=1)
    bat_az = bat[bat["Team"] == FG_TEAM][["Name", "WAR"]].copy()
    bat_az["position_type"] = "Batter"

    print(f"Fetching FanGraphs pitching stats for {season}...")
    pit = pb.pitching_stats(season, qual=1)
    pit_az = pit[pit["Team"] == FG_TEAM][["Name", "WAR"]].copy()
    pit_az["position_type"] = "Pitcher"

    return pd.concat([bat_az, pit_az], ignore_index=True)


# ---------------------------------------------------------------------------
# Salary from Spotrac
# ---------------------------------------------------------------------------

def fetch_salary_spotrac(season: int = 2025) -> pd.DataFrame:
    """Scrape Spotrac team payroll page. Returns DataFrame: Name, Salary."""
    url = f"https://www.spotrac.com/mlb/{SPOTRAC_SLUG}/payroll/{season}/"
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

    df = pd.DataFrame(rows).drop_duplicates(subset=["Name"])
    print(f"  Found {len(df)} salary rows from Spotrac.")
    return df


# ---------------------------------------------------------------------------
# Merge WAR + Salary and save
# ---------------------------------------------------------------------------

def fetch_war_salary(season: int = 2025, skip_salary: bool = False):
    """Fetch WAR from FanGraphs, salary from Spotrac, merge by name, save CSV."""
    war_df = fetch_war(season)

    if not skip_salary:
        try:
            sal_df = fetch_salary_spotrac(season)
            # Normalize names for matching
            war_df["_norm"] = war_df["Name"].apply(_normalize)
            sal_df["_norm"] = sal_df["Name"].apply(_normalize)
            merged = war_df.merge(sal_df[["_norm", "Salary"]], on="_norm", how="left")
            merged = merged.drop(columns=["_norm"])
            # Also include salary-only players (not in FanGraphs stats — injured, etc.)
            sal_only = sal_df[~sal_df["_norm"].isin(war_df["_norm"].values)].copy()
            if not sal_only.empty:
                sal_only = sal_only.rename(columns={"Name": "Name"})
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

    out_path = os.path.join(RAW_DIR, f"value_{season}.csv")
    merged.to_csv(out_path, index=False)
    print(f"Saved {len(merged)} rows to {out_path}")
    print(merged[["Name", "WAR", "Salary", "position_type"]].to_string(index=False))


# ---------------------------------------------------------------------------
# Awards from MLB Stats API
# ---------------------------------------------------------------------------

def fetch_awards(season: int = 2025):
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
    parser.add_argument("--war-only", action="store_true", help="Only fetch WAR (no salary scrape)")
    parser.add_argument("--salary-only", action="store_true", help="Only scrape and print salary")
    parser.add_argument("--awards-only", action="store_true", help="Only fetch awards")
    args = parser.parse_args()

    os.makedirs(RAW_DIR, exist_ok=True)

    if args.salary_only:
        df = fetch_salary_spotrac(args.season)
        print(df.to_string(index=False))
    elif args.awards_only:
        fetch_awards(args.season)
    elif args.war_only:
        fetch_war_salary(args.season, skip_salary=True)
    else:
        fetch_war_salary(args.season)
        fetch_awards(args.season)
