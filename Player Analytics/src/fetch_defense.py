"""
fetch_defense.py — Fetch defensive metrics for D-backs players.

Data sources:
  - FanGraphs fielding stats via pybaseball (DRS, Def, OAA, FP, G, Inn)
  - Statcast sprint speed via pybaseball (ft/sec, computed percentile)

Usage:
    python src/fetch_defense.py              # default season 2025
    python src/fetch_defense.py --season 2024
"""

import argparse
import os
import sqlite3
import unicodedata
import difflib

import pandas as pd
import pybaseball as pb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
RAW_DIR  = os.path.join(DATA_DIR, "raw")
DB_PATH  = os.path.join(DATA_DIR, "db", "dbacks.db")

FG_TEAM = "ARI"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    name = str(name).strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    for suffix in [" jr.", " sr.", " ii", " iii", " iv"]:
        name = name.replace(suffix, "")
    return name.strip()


def _load_roster(season: int):
    """Return list of (player_id, full_name) from DB for the given season."""
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}. Run load_db.py first.")
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT player_id, full_name FROM players WHERE season=?", (season,)
    ).fetchall()
    conn.close()
    return rows  # list of (int, str)


# ---------------------------------------------------------------------------
# Fielding stats from FanGraphs
# ---------------------------------------------------------------------------

def fetch_fielding(season: int = 2025) -> pd.DataFrame:
    """Return one fielding row per ARI player (primary position + aggregated totals)."""
    roster = _load_roster(season)
    if not roster:
        return pd.DataFrame()

    roster_norms = {_normalize(name): pid for pid, name in roster}

    print(f"Fetching FanGraphs fielding stats for {season}...")
    f = pb.fielding_stats(season, qual=1)

    # Filter to ARI team rows
    ari = f[f["Team"] == FG_TEAM].copy()
    print(f"  Found {len(ari)} ARI fielding rows across all positions.")

    # Normalize names for matching
    ari["_norm"] = ari["Name"].apply(_normalize)

    results = []
    for norm, pid in roster_norms.items():
        # Match by normalized name — exact first, then fuzzy
        matched = ari[ari["_norm"] == norm]
        if matched.empty:
            close = difflib.get_close_matches(norm, ari["_norm"].tolist(), n=1, cutoff=0.82)
            if close:
                matched = ari[ari["_norm"] == close[0]]
        if matched.empty:
            continue

        # Primary position = row with most innings
        primary = matched.sort_values("Inn", ascending=False).iloc[0]

        # Aggregate DRS, Def, OAA across all positions
        drs_total  = matched["DRS"].sum()    if "DRS" in matched.columns else None
        def_total  = matched["Def"].sum()    if "Def" in matched.columns else None
        oaa_total  = matched["OAA"].sum()    if "OAA" in matched.columns else None

        results.append({
            "player_id":    pid,
            "position":     primary.get("Pos"),
            "games":        int(primary.get("G", 0) or 0),
            "innings":      float(primary.get("Inn", 0) or 0),
            "errors":       int(primary.get("E", 0) or 0),
            "fielding_pct": float(primary.get("FP", 0) or 0),
            "drs":          float(drs_total) if pd.notna(drs_total) else None,
            "def_runs":     float(def_total) if pd.notna(def_total) else None,
            "oaa":          float(oaa_total) if (oaa_total is not None and pd.notna(oaa_total)) else None,
        })

    df = pd.DataFrame(results)
    print(f"  Matched {len(df)} ARI players with fielding data.")
    return df


# ---------------------------------------------------------------------------
# Sprint speed from Statcast
# ---------------------------------------------------------------------------

def fetch_sprint(season: int = 2025) -> pd.DataFrame:
    """Return sprint speed + computed percentile for ARI players."""
    roster = _load_roster(season)
    if not roster:
        return pd.DataFrame()

    roster_ids = {pid for pid, _ in roster}

    print(f"Fetching Statcast sprint speed for {season}...")
    s = pb.statcast_sprint_speed(season)

    # Compute league-wide percentile rank
    s = s.dropna(subset=["sprint_speed"]).copy()
    s["sprint_pct"] = s["sprint_speed"].rank(pct=True).mul(100).round(0).astype(int)

    # Filter to ARI players by MLBAM player_id
    ari_sprint = s[s["player_id"].isin(roster_ids)][["player_id", "sprint_speed", "sprint_pct"]]
    print(f"  Found sprint speed for {len(ari_sprint)} ARI players.")
    return ari_sprint.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Merge and save
# ---------------------------------------------------------------------------

def fetch_defense(season: int = 2025):
    """Fetch fielding + sprint speed, merge by player_id, save CSV."""
    os.makedirs(RAW_DIR, exist_ok=True)

    fielding_df = fetch_fielding(season)
    sprint_df   = fetch_sprint(season)

    if fielding_df.empty and sprint_df.empty:
        print("No data fetched.")
        return

    # Get all roster player_ids as base
    roster = _load_roster(season)
    base = pd.DataFrame(roster, columns=["player_id", "full_name"])

    # Merge fielding
    if not fielding_df.empty:
        merged = base.merge(fielding_df, on="player_id", how="left")
    else:
        merged = base.copy()
        for col in ["position","games","innings","errors","fielding_pct","drs","def_runs","oaa"]:
            merged[col] = None

    # Merge sprint
    if not sprint_df.empty:
        merged = merged.merge(sprint_df, on="player_id", how="left")
    else:
        merged["sprint_speed"] = None
        merged["sprint_pct"]   = None

    out_path = os.path.join(RAW_DIR, f"defense_{season}.csv")
    merged.to_csv(out_path, index=False)
    print(f"\nSaved {len(merged)} rows to {out_path}")

    # Print players with any data
    has_data = merged[merged[["drs","sprint_speed"]].notna().any(axis=1)]
    print(has_data[["full_name","position","drs","def_runs","oaa","sprint_speed","sprint_pct"]].to_string(index=False))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch defensive metrics")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()
    fetch_defense(args.season)
