"""
app.py — Flask web application for D-backs analytics.
"""

import json
import os
import sys
import sqlite3

from flask import Flask, jsonify, render_template, abort, request, redirect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Allow importing from src/
sys.path.insert(0, os.path.join(BASE_DIR, "..", "src"))

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)


@app.before_request
def force_https():
    # Railway sets X-Forwarded-Proto header; redirect http -> https in production
    if request.headers.get("X-Forwarded-Proto") == "http":
        return redirect(request.url.replace("http://", "https://", 1), code=301)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def build_pitch_filter(player_col: str, player_id: int) -> tuple[str, list]:
    """Build SQL WHERE clause from URL query params."""
    clauses = [f"{player_col} = ?"]
    params = [player_id]

    inning = request.args.get("inning")
    if inning:
        if inning == "9+":
            clauses.append("inning >= 9")
        else:
            clauses.append("inning = ?")
            params.append(int(inning))

    balls = request.args.get("balls")
    strikes = request.args.get("strikes")
    if balls is not None and strikes is not None:
        clauses.append("balls = ? AND strikes = ?")
        params.extend([int(balls), int(strikes)])

    p_throws = request.args.get("p_throws")
    if p_throws in ("L", "R"):
        clauses.append("p_throws = ?")
        params.append(p_throws)

    stand = request.args.get("stand")
    if stand in ("L", "R"):
        clauses.append("stand = ?")
        params.append(stand)

    runners = request.args.get("runners")
    if runners == "empty":
        clauses.append("on_1b IS NULL AND on_2b IS NULL AND on_3b IS NULL")
    elif runners == "risp":
        clauses.append("(on_2b IS NOT NULL OR on_3b IS NOT NULL)")
    elif runners == "loaded":
        clauses.append("on_1b IS NOT NULL AND on_2b IS NOT NULL AND on_3b IS NOT NULL")
    elif runners == "on1b":
        clauses.append("on_1b IS NOT NULL AND on_2b IS NULL AND on_3b IS NULL")

    home_away = request.args.get("home_away")
    if home_away == "home":
        clauses.append("home_team = 'AZ'")
    elif home_away == "away":
        clauses.append("home_team != 'AZ'")

    outs = request.args.get("outs")
    if outs is not None:
        clauses.append("outs_when_up = ?")
        params.append(int(outs))

    pitch_type = request.args.get("pitch_type")
    if pitch_type:
        clauses.append("pitch_type = ?")
        params.append(pitch_type)

    return " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# Live split computation (used when filters are active)
# ---------------------------------------------------------------------------

RUNNER_LABELS = {
    0: "Bases Empty", 1: "Runner on 1B", 2: "Runner on 2B",
    3: "Runners on 1B-2B", 4: "Runner on 3B", 5: "Runners on 1B-3B",
    6: "Runners on 2B-3B", 7: "Bases Loaded",
}

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


def _compute_split_groups(df, player_type: str, split_type: str) -> list:
    """
    Given a filtered pitch DataFrame, group by split_type and compute stats.
    Imports compute functions lazily from process.py.
    """
    from process import compute_batter_stats, compute_pitcher_stats

    compute_fn = compute_batter_stats if player_type == "batter" else compute_pitcher_stats

    results = []

    def add(sv, sub):
        if len(sub) == 0:
            return
        stats = compute_fn(sub)
        if stats:
            results.append({"split_type": split_type, "split_value": sv, **stats})

    if split_type == "inning":
        for v in sorted(df["inning"].dropna().unique()):
            lbl = f"{int(v)}+" if int(v) >= 9 else str(int(v))
            add(lbl, df[df["inning"] == v])

    elif split_type == "count":
        for b in range(4):
            for s in range(3):
                add(f"{b}-{s}", df[(df["balls"] == b) & (df["strikes"] == s)])

    elif split_type == "runners":
        df = df.copy()
        df["_rs"] = (
            df["on_1b"].notna().astype(int) * 1
            + df["on_2b"].notna().astype(int) * 2
            + df["on_3b"].notna().astype(int) * 4
        )
        for code, label in RUNNER_LABELS.items():
            add(label, df[df["_rs"] == code])
        add("RISP", df[(df["on_2b"].notna()) | (df["on_3b"].notna())])

    elif split_type == "outs":
        for o in [0, 1, 2]:
            add(str(o), df[df["outs_when_up"] == o])

    elif split_type == "handedness":
        if player_type == "batter":
            add("vs LHP", df[df["p_throws"] == "L"])
            add("vs RHP", df[df["p_throws"] == "R"])
        else:
            add("vs LHB", df[df["stand"] == "L"])
            add("vs RHB", df[df["stand"] == "R"])

    elif split_type == "venue_type":
        if player_type == "pitcher":
            add("Home", df[df["home_team"] == "AZ"])
            add("Away", df[df["home_team"] != "AZ"])
        else:
            bt = df.apply(
                lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"],
                axis=1,
            )
            add("Home", df[bt == df["home_team"]])
            add("Away", df[bt != df["home_team"]])

    elif split_type == "pitch_type":
        for pt in sorted(df["pitch_type"].dropna().unique()):
            add(pt, df[df["pitch_type"] == pt])

    elif split_type == "stadium":
        for team_code, venue_name in VENUE_MAP.items():
            sub = df[df["home_team"] == team_code]
            if len(sub) > 0:
                add(venue_name, sub)

    elif split_type == "leverage":
        add("Two Strikes", df[df["strikes"] == 2])
        add("Full Count", df[(df["balls"] == 3) & (df["strikes"] == 2)])
        add("Hitters Count", df[(df["balls"] >= df["strikes"]) & (df["balls"] >= 2)])

    elif split_type == "score_state":
        if "bat_score" in df.columns and "fld_score" in df.columns:
            diff = df["bat_score"] - df["fld_score"]
            add("Losing (>2)",   df[diff < -2])
            add("Losing (1-2)",  df[diff.between(-2, -1)])
            add("Tied",          df[diff == 0])
            add("Leading (1-2)", df[diff.between(1, 2)])
            add("Leading (>2)",  df[diff > 2])

    # Prepend overall (filtered) row
    from process import compute_batter_stats, compute_pitcher_stats
    overall = compute_fn(df)
    if overall:
        results.insert(0, {"split_type": split_type, "split_value": "All (filtered)", **overall})

    return results


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    batters = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number "
        "FROM players p "
        "WHERE p.position_type != 'Pitcher' AND p.season = 2025 "
        "ORDER BY p.full_name"
    )
    pitchers = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number "
        "FROM players p "
        "WHERE p.position_type = 'Pitcher' AND p.season = 2025 "
        "ORDER BY p.full_name"
    )
    # Classify pitchers as SP vs RP: starters pitched in inning 1 in 3+ games
    starter_rows = query(
        "SELECT pitcher FROM pitches "
        "WHERE pitcher IN (SELECT player_id FROM players WHERE position_type='Pitcher' AND season=2025) "
        "AND inning = 1 "
        "GROUP BY pitcher HAVING COUNT(DISTINCT game_pk) >= 3"
    )
    starter_ids = {r["pitcher"] for r in starter_rows}
    for p in pitchers:
        p["position"] = "SP" if p["player_id"] in starter_ids else "RP"
    return render_template("index.html", batters=batters, pitchers=pitchers)


@app.route("/batter/<int:player_id>")
def batter_page(player_id: int):
    player = query("SELECT * FROM players WHERE player_id=? AND season=2025", (player_id,))
    if not player:
        abort(404)
    player = player[0]
    if player["position_type"] == "Pitcher":
        abort(404)
    overall = query(
        "SELECT * FROM batter_splits WHERE player_id=? AND season=2025 AND split_type='overall'",
        (player_id,)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM batter_splits "
        "WHERE player_id=? AND season=2025 ORDER BY split_type", (player_id,)
    )]
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE batter=? AND pitch_type IS NOT NULL "
        "ORDER BY pitch_type", (player_id,)
    )]
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="batter")


@app.route("/pitcher/<int:player_id>")
def pitcher_page(player_id: int):
    player = query("SELECT * FROM players WHERE player_id=? AND season=2025", (player_id,))
    if not player:
        abort(404)
    player = player[0]
    if player["position_type"] != "Pitcher":
        abort(404)
    overall = query(
        "SELECT * FROM pitcher_splits WHERE player_id=? AND season=2025 AND split_type='overall'",
        (player_id,)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM pitcher_splits "
        "WHERE player_id=? AND season=2025 ORDER BY split_type", (player_id,)
    )]
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL "
        "ORDER BY pitch_type", (player_id,)
    )]
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="pitcher")


# ---------------------------------------------------------------------------
# API — Pre-computed splits (no active filters)
# ---------------------------------------------------------------------------

@app.route("/api/players")
def api_players():
    players = query(
        "SELECT player_id, full_name, position, position_type, jersey_number "
        "FROM players WHERE season=2025 ORDER BY position_type DESC, full_name"
    )
    return jsonify(players)


@app.route("/api/batter/<int:player_id>/<split_type>")
def api_batter_split(player_id: int, split_type: str):
    rows = query(
        "SELECT * FROM batter_splits WHERE player_id=? AND season=2025 AND split_type=? "
        "ORDER BY split_value",
        (player_id, split_type)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/<split_type>")
def api_pitcher_split(player_id: int, split_type: str):
    rows = query(
        "SELECT * FROM pitcher_splits WHERE player_id=? AND season=2025 AND split_type=? "
        "ORDER BY split_value",
        (player_id, split_type)
    )
    for r in rows:
        if r.get("pitch_mix"):
            try:
                r["pitch_mix"] = json.loads(r["pitch_mix"])
            except Exception:
                pass
    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — Live splits (computed on-the-fly when filters are active)
# ---------------------------------------------------------------------------

@app.route("/api/batter/<int:player_id>/splits_live")
def api_batter_splits_live(player_id: int):
    import pandas as pd
    split_type = request.args.get("split_type", "count")
    where, params = build_pitch_filter("batter", player_id)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(f"SELECT * FROM pitches WHERE {where}",
                           conn, params=tuple(params))
    conn.close()
    if df.empty:
        return jsonify([])
    return jsonify(_compute_split_groups(df, "batter", split_type))


@app.route("/api/pitcher/<int:player_id>/splits_live")
def api_pitcher_splits_live(player_id: int):
    import pandas as pd
    split_type = request.args.get("split_type", "count")
    where, params = build_pitch_filter("pitcher", player_id)
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(f"SELECT * FROM pitches WHERE {where}",
                           conn, params=tuple(params))
    conn.close()
    if df.empty:
        return jsonify([])
    return jsonify(_compute_split_groups(df, "pitcher", split_type))


# ---------------------------------------------------------------------------
# API — Raw pitch data (heat maps, spray, movement)
# ---------------------------------------------------------------------------

@app.route("/api/batter/<int:player_id>/pitches")
def api_batter_pitches(player_id: int):
    where, params = build_pitch_filter("batter", player_id)
    rows = query(
        f"SELECT plate_x, plate_z, type, description, events, bb_type, "
        f"hc_x, hc_y, launch_speed, launch_angle, pitch_type, p_throws, "
        f"release_speed, inning, balls, strikes, outs_when_up "
        f"FROM pitches WHERE {where} LIMIT 15000",
        tuple(params)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/pitches")
def api_pitcher_pitches(player_id: int):
    where, params = build_pitch_filter("pitcher", player_id)
    rows = query(
        f"SELECT plate_x, plate_z, type, description, events, pitch_type, "
        f"release_speed, pfx_x, pfx_z, stand, inning, balls, strikes "
        f"FROM pitches WHERE {where} LIMIT 15000",
        tuple(params)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/movement")
def api_pitcher_movement(player_id: int):
    rows = query(
        "SELECT pitch_type, pfx_x, pfx_z, release_speed, release_spin_rate "
        "FROM pitches WHERE pitcher=? "
        "AND pfx_x IS NOT NULL AND pfx_z IS NOT NULL AND pitch_type IS NOT NULL "
        "LIMIT 8000",
        (player_id,)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/velocity")
def api_pitcher_velocity(player_id: int):
    rows = query(
        "SELECT pitch_type, COUNT(*) as pitches, "
        "AVG(release_speed) as avg_velo, MAX(release_speed) as max_velo, "
        "MIN(release_speed) as min_velo, AVG(release_spin_rate) as avg_spin "
        "FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL "
        "GROUP BY pitch_type ORDER BY pitches DESC",
        (player_id,)
    )
    total = sum(r["pitches"] for r in rows)
    for r in rows:
        r["usage_pct"] = round(r["pitches"] / total * 100, 1) if total > 0 else 0
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"WARNING: Database not found at {DB_PATH}")
        print("Run: python run_pipeline.py")
    app.run(debug=True, port=5000)
