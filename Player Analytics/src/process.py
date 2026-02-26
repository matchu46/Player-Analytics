"""
process.py — Compute situational splits from the pitches table and write
             results into batter_splits / pitcher_splits tables.

Usage:
    python src/process.py --batters    # Process batter splits only
    python src/process.py --pitchers   # Process pitcher splits only
    python src/process.py --all        # Both (default)
    python src/process.py --player 682998   # Single player
"""

import argparse
import json
import os
import sqlite3
import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")

SEASON = 2025
ARI_CODE = "AZ"   # Statcast stores Arizona as 'AZ', not 'ARI'

# ---------------------------------------------------------------------------
# Helpers — counting stats from a filtered DataFrame
# ---------------------------------------------------------------------------

# Events that end a plate appearance
PA_EVENTS = {
    "single", "double", "triple", "home_run",
    "walk", "strikeout", "strikeout_double_play",
    "field_out", "grounded_into_double_play", "double_play", "triple_play",
    "force_out", "sac_fly", "sac_bunt", "sac_fly_double_play",
    "fielders_choice", "fielders_choice_out",
    "hit_by_pitch", "catcher_interf",
    "field_error",
}

AB_EXCLUDE = {"walk", "hit_by_pitch", "sac_fly", "sac_bunt", "catcher_interf"}


def compute_batter_stats(df: pd.DataFrame) -> dict:
    """Given a DataFrame of pitches for one situational slice, compute batting stats."""
    # Terminal pitches only (end of PA)
    pa_df = df[df["events"].notna() & df["events"].isin(PA_EVENTS)]

    pa = len(pa_df)
    if pa == 0:
        return {}

    hits = pa_df["events"].isin(["single", "double", "triple", "home_run"]).sum()
    singles = (pa_df["events"] == "single").sum()
    doubles = (pa_df["events"] == "double").sum()
    triples = (pa_df["events"] == "triple").sum()
    home_runs = (pa_df["events"] == "home_run").sum()
    walks = pa_df["events"].isin(["walk"]).sum()
    hbp = (pa_df["events"] == "hit_by_pitch").sum()
    strikeouts = pa_df["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    ab = pa - pa_df["events"].isin(AB_EXCLUDE).sum()

    avg = hits / ab if ab > 0 else None
    obp = (hits + walks + hbp) / pa if pa > 0 else None
    tb = singles + 2 * doubles + 3 * triples + 4 * home_runs
    slg = tb / ab if ab > 0 else None
    ops = (obp or 0) + (slg or 0) if obp is not None and slg is not None else None

    # wOBA (simplified linear weights — MLB average 2025 weights approximate)
    woba_weights = {"walk": 0.696, "hit_by_pitch": 0.726, "single": 0.883,
                    "double": 1.244, "triple": 1.569, "home_run": 2.007}
    woba_num = sum(
        pa_df["events"].isin([ev]).sum() * w for ev, w in woba_weights.items()
    )
    woba_denom = ab + walks + hbp + pa_df["events"].isin(["sac_fly"]).sum()
    woba = woba_num / woba_denom if woba_denom > 0 else None

    # Batted ball metrics (pitches with exit velo data)
    bip = df[df["launch_speed"].notna()]
    avg_ev = bip["launch_speed"].mean() if len(bip) > 0 else None
    avg_la = bip["launch_angle"].mean() if len(bip) > 0 else None
    hard_hit = (bip["launch_speed"] >= 95).sum() / len(bip) if len(bip) > 0 else None

    # Barrel: EV >= 98 mph + launch angle 26-30, simplified
    barrel_mask = (bip["launch_speed"] >= 98) & bip["launch_angle"].between(26, 30)
    barrel_pct = barrel_mask.sum() / len(bip) if len(bip) > 0 else None

    # Swing / contact
    swings = df["description"].isin([
        "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
        "hit_into_play", "hit_into_play_no_out", "hit_into_play_score",
        "foul_bunt", "missed_bunt",
    ])
    swing_pct = swings.sum() / len(df) if len(df) > 0 else None
    whiffs = df["description"].isin(["swinging_strike", "swinging_strike_blocked", "missed_bunt"])
    whiff_pct = whiffs.sum() / swings.sum() if swings.sum() > 0 else None
    contacts = df["description"].isin([
        "foul", "foul_tip", "hit_into_play", "hit_into_play_no_out",
        "hit_into_play_score", "foul_bunt",
    ])
    contact_pct = contacts.sum() / swings.sum() if swings.sum() > 0 else None

    return {
        "pa": int(pa), "ab": int(ab), "hits": int(hits),
        "singles": int(singles), "doubles": int(doubles),
        "triples": int(triples), "home_runs": int(home_runs),
        "walks": int(walks), "strikeouts": int(strikeouts), "hbp": int(hbp),
        "avg": round(avg, 3) if avg is not None else None,
        "obp": round(obp, 3) if obp is not None else None,
        "slg": round(slg, 3) if slg is not None else None,
        "ops": round(ops, 3) if ops is not None else None,
        "woba": round(woba, 3) if woba is not None else None,
        "avg_exit_velo": round(avg_ev, 1) if avg_ev is not None else None,
        "avg_launch_angle": round(avg_la, 1) if avg_la is not None else None,
        "hard_hit_pct": round(hard_hit, 3) if hard_hit is not None else None,
        "barrel_pct": round(barrel_pct, 3) if barrel_pct is not None else None,
        "swing_pct": round(swing_pct, 3) if swing_pct is not None else None,
        "whiff_pct": round(whiff_pct, 3) if whiff_pct is not None else None,
        "contact_pct": round(contact_pct, 3) if contact_pct is not None else None,
    }


def compute_pitcher_stats(df: pd.DataFrame) -> dict:
    """Given pitches thrown by one pitcher in a situational slice, compute pitching stats."""
    pa_df = df[df["events"].notna() & df["events"].isin(PA_EVENTS)]
    bf = len(pa_df)
    if bf == 0:
        return {}

    hits = pa_df["events"].isin(["single", "double", "triple", "home_run"]).sum()
    walks = (pa_df["events"] == "walk").sum()
    hbp = (pa_df["events"] == "hit_by_pitch").sum()
    hrs = (pa_df["events"] == "home_run").sum()
    ks = pa_df["events"].isin(["strikeout", "strikeout_double_play"]).sum()
    ab = bf - pa_df["events"].isin(AB_EXCLUDE).sum()

    avg_against = hits / ab if ab > 0 else None
    k_pct = ks / bf if bf > 0 else None
    bb_pct = walks / bf if bf > 0 else None
    k_bb = ks / walks if walks > 0 else None

    obp_denom = ab + walks + hbp
    obp_against = (hits + walks + hbp) / obp_denom if obp_denom > 0 else None
    tb = (pa_df["events"] == "single").sum() + \
         2 * (pa_df["events"] == "double").sum() + \
         3 * (pa_df["events"] == "triple").sum() + \
         4 * hrs
    slg_against = tb / ab if ab > 0 else None

    woba_weights = {"walk": 0.696, "hit_by_pitch": 0.726, "single": 0.883,
                    "double": 1.244, "triple": 1.569, "home_run": 2.007}
    woba_num = sum(pa_df["events"].isin([ev]).sum() * w for ev, w in woba_weights.items())
    woba_denom = ab + walks + hbp + pa_df["events"].isin(["sac_fly"]).sum()
    woba_against = woba_num / woba_denom if woba_denom > 0 else None

    # Pitch mix
    pitch_counts = df["pitch_type"].value_counts()
    total_pitches = pitch_counts.sum()
    pitch_mix = {pt: round(cnt / total_pitches * 100, 1)
                 for pt, cnt in pitch_counts.items()} if total_pitches > 0 else {}

    avg_velo = df["release_speed"].mean() if df["release_speed"].notna().any() else None
    avg_spin = df["release_spin_rate"].mean() if df["release_spin_rate"].notna().any() else None

    return {
        "batters_faced": int(bf),
        "hits_allowed": int(hits),
        "home_runs_allowed": int(hrs),
        "walks_allowed": int(walks),
        "strikeouts": int(ks),
        "hbp": int(hbp),
        "k_pct": round(k_pct, 3) if k_pct is not None else None,
        "bb_pct": round(bb_pct, 3) if bb_pct is not None else None,
        "k_bb": round(k_bb, 2) if k_bb is not None else None,
        "avg_against": round(avg_against, 3) if avg_against is not None else None,
        "obp_against": round(obp_against, 3) if obp_against is not None else None,
        "slg_against": round(slg_against, 3) if slg_against is not None else None,
        "woba_against": round(woba_against, 3) if woba_against is not None else None,
        "pitch_mix": json.dumps(pitch_mix),
        "avg_velo": round(avg_velo, 1) if avg_velo is not None else None,
        "avg_spin_rate": round(avg_spin, 0) if avg_spin is not None else None,
    }


# ---------------------------------------------------------------------------
# Split definitions
# ---------------------------------------------------------------------------

VENUE_MAP = {
    "AZ": "Chase Field", "ATL": "Truist Park", "BAL": "Camden Yards",
    "BOS": "Fenway Park", "CHC": "Wrigley Field", "CWS": "Guaranteed Rate Field",
    "CIN": "Great American Ball Park", "CLE": "Progressive Field",
    "COL": "Coors Field", "DET": "Comerica Park", "HOU": "Minute Maid Park",
    "KC": "Kauffman Stadium", "LAA": "Angel Stadium", "LAD": "Dodger Stadium",
    "MIA": "loanDepot Park", "MIL": "American Family Field", "MIN": "Target Field",
    "NYM": "Citi Field", "NYY": "Yankee Stadium", "ATH": "Sutter Health Park",
    "PHI": "Citizens Bank Park", "PIT": "PNC Park", "SD": "Petco Park",
    "SF": "Oracle Park", "SEA": "T-Mobile Park", "STL": "Busch Stadium",
    "TB": "Tropicana Field", "TEX": "Globe Life Field", "TOR": "Rogers Centre",
    "WSH": "Nationals Park",
}


def get_splits(df: pd.DataFrame, player_col: str) -> list[dict]:
    """
    Given a DataFrame of pitches for ONE player (identified by player_col being their ID),
    compute all situational splits and return as list of dicts ready for DB insertion.
    """
    splits = []

    def add(split_type, split_value, sub_df, compute_fn):
        if len(sub_df) == 0:
            return
        stats = compute_fn(sub_df)
        if not stats:
            return
        splits.append({"split_type": split_type, "split_value": str(split_value), **stats})

    compute_fn = compute_batter_stats if player_col == "batter" else compute_pitcher_stats

    # --- Overall ---
    add("overall", "All", df, compute_fn)

    # --- By inning ---
    for inning in sorted(df["inning"].dropna().unique()):
        add("inning", str(int(inning)), df[df["inning"] == inning], compute_fn)

    # --- By count ---
    for balls in range(4):
        for strikes in range(3):
            label = f"{balls}-{strikes}"
            add("count", label, df[(df["balls"] == balls) & (df["strikes"] == strikes)], compute_fn)

    # --- By runner state ---
    runner_labels = {
        0: "Bases Empty",
        1: "Runner on 1B",
        2: "Runner on 2B",
        3: "Runners on 1B-2B",
        4: "Runner on 3B",
        5: "Runners on 1B-3B",
        6: "Runners on 2B-3B",
        7: "Bases Loaded",
    }
    df = df.copy()
    df["runner_state"] = (
        df["on_1b"].notna().astype(int) * 1
        + df["on_2b"].notna().astype(int) * 2
        + df["on_3b"].notna().astype(int) * 4
    )
    for code, label in runner_labels.items():
        add("runners", label, df[df["runner_state"] == code], compute_fn)

    # RISP
    risp_df = df[(df["on_2b"].notna()) | (df["on_3b"].notna())]
    add("runners", "RISP", risp_df, compute_fn)

    # --- By outs ---
    for outs in [0, 1, 2]:
        add("outs", str(outs), df[df["outs_when_up"] == outs], compute_fn)

    # --- By pitcher/batter handedness ---
    if player_col == "batter":
        add("handedness", "vs LHP", df[df["p_throws"] == "L"], compute_fn)
        add("handedness", "vs RHP", df[df["p_throws"] == "R"], compute_fn)
    else:
        add("handedness", "vs LHB", df[df["stand"] == "L"], compute_fn)
        add("handedness", "vs RHB", df[df["stand"] == "R"], compute_fn)

    # --- Home / Away ---
    if player_col == "batter":
        add("venue_type", "Home", df[df["batting_team"] == df["home_team"]], compute_fn)
        add("venue_type", "Away", df[df["batting_team"] != df["home_team"]], compute_fn)
    else:
        add("venue_type", "Home", df[df["home_team"] == ARI_CODE], compute_fn)
        add("venue_type", "Away", df[df["home_team"] != ARI_CODE], compute_fn)

    # --- By specific stadium ---
    for team_code, venue_name in VENUE_MAP.items():
        sub = df[df["home_team"] == team_code]
        add("stadium", venue_name, sub, compute_fn)

    # --- By pitch type (pitchers) or pitch faced (batters) ---
    for pt in df["pitch_type"].dropna().unique():
        add("pitch_type", pt, df[df["pitch_type"] == pt], compute_fn)

    # --- Day / Night (inferred from game_date — approximate; true day/night requires schedule) ---
    # We can't determine day/night from Statcast alone without schedule data; skip for now

    # --- By score differential ---
    if "bat_score" in df.columns and "fld_score" in df.columns:
        df["score_diff"] = df["bat_score"] - df["fld_score"]
        add("score_state", "Losing (>2)", df[df["score_diff"] < -2], compute_fn)
        add("score_state", "Losing (1-2)", df[df["score_diff"].between(-2, -1)], compute_fn)
        add("score_state", "Tied", df[df["score_diff"] == 0], compute_fn)
        add("score_state", "Leading (1-2)", df[df["score_diff"].between(1, 2)], compute_fn)
        add("score_state", "Leading (>2)", df[df["score_diff"] > 2], compute_fn)

    # --- High-leverage counts (2 strikes, full count) ---
    add("leverage", "Two Strikes", df[df["strikes"] == 2], compute_fn)
    add("leverage", "Full Count", df[(df["balls"] == 3) & (df["strikes"] == 2)], compute_fn)
    add("leverage", "Hitters Count", df[(df["balls"] >= df["strikes"]) & (df["balls"] >= 2)], compute_fn)
    add("leverage", "Late & Close (7+, within 2)",
        df[(df["inning"] >= 7) & (df["score_diff"].abs() <= 2)] if "score_diff" in df.columns else df[df["inning"] >= 7],
        compute_fn)

    return splits


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_batters(player_id: int = None, season: int = SEASON):
    conn = sqlite3.connect(DB_PATH)

    # Get all ARI batters from roster
    if player_id:
        players = pd.read_sql(
            "SELECT player_id, full_name FROM players WHERE player_id=? AND season=?",
            conn, params=(player_id, season)
        )
    else:
        players = pd.read_sql(
            "SELECT player_id, full_name FROM players WHERE position_type != 'Pitcher' AND season=?",
            conn, params=(season,)
        )

    print(f"Processing {len(players)} batter(s)...")
    for _, row in players.iterrows():
        pid = row["player_id"]
        name = row["full_name"]
        print(f"  {name} ({pid})...")

        df = pd.read_sql(
            "SELECT * FROM pitches WHERE batter=?",
            conn, params=(pid,)
        )
        if df.empty:
            print(f"    No pitch data found.")
            continue

        # Always recompute batting_team (stored column may be NULL)
        df["batting_team"] = df.apply(
            lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"],
            axis=1
        )

        splits = get_splits(df, player_col="batter")
        rows = [{"player_id": pid, "season": season, **s} for s in splits]
        splits_df = pd.DataFrame(rows)

        # Upsert into batter_splits
        conn.execute("DELETE FROM batter_splits WHERE player_id=? AND season=?", (pid, season))
        splits_df.to_sql("batter_splits", conn, if_exists="append", index=False)
        conn.commit()
        print(f"    Wrote {len(splits_df)} split rows.")

    conn.close()


def process_pitchers(player_id: int = None, season: int = SEASON):
    conn = sqlite3.connect(DB_PATH)

    if player_id:
        players = pd.read_sql(
            "SELECT player_id, full_name FROM players WHERE player_id=? AND season=?",
            conn, params=(player_id, season)
        )
    else:
        players = pd.read_sql(
            "SELECT player_id, full_name FROM players WHERE position_type = 'Pitcher' AND season=?",
            conn, params=(season,)
        )

    print(f"Processing {len(players)} pitcher(s)...")
    for _, row in players.iterrows():
        pid = row["player_id"]
        name = row["full_name"]
        print(f"  {name} ({pid})...")

        df = pd.read_sql(
            "SELECT * FROM pitches WHERE pitcher=?",
            conn, params=(pid,)
        )
        if df.empty:
            print(f"    No pitch data found.")
            continue

        splits = get_splits(df, player_col="pitcher")
        rows = [{"player_id": pid, "season": season, **s} for s in splits]
        splits_df = pd.DataFrame(rows)

        conn.execute("DELETE FROM pitcher_splits WHERE player_id=? AND season=?", (pid, season))
        splits_df.to_sql("pitcher_splits", conn, if_exists="append", index=False)
        conn.commit()
        print(f"    Wrote {len(splits_df)} split rows.")

    conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute D-backs situational splits")
    parser.add_argument("--batters", action="store_true")
    parser.add_argument("--pitchers", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--player", type=int, default=None, help="Single player MLBAM ID")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()

    if args.all or (not args.batters and not args.pitchers):
        process_batters(player_id=args.player, season=args.season)
        process_pitchers(player_id=args.player, season=args.season)
    elif args.batters:
        process_batters(player_id=args.player, season=args.season)
    elif args.pitchers:
        process_pitchers(player_id=args.player, season=args.season)
