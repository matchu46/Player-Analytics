"""
app.py — Flask web application for MLB team analytics.
"""

import json
import os
import sys
import sqlite3

from flask import Flask, jsonify, render_template, abort, request, redirect, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
DB_PATH = os.path.join(DATA_DIR, "db", "baseball.db")
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Allow importing from src/
sys.path.insert(0, os.path.join(BASE_DIR, "..", "src"))

from teams import TEAMS, SEASON

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
    if home_away in ("home", "away"):
        # Look up the player's team statcast code dynamically
        player_rows = query("SELECT team FROM players WHERE player_id=?", (player_id,))
        team_code = player_rows[0]['team'] if player_rows else 'ARI'
        statcast_code = TEAMS.get(team_code, TEAMS['ARI'])['statcast_code']
        if home_away == "home":
            clauses.append(f"home_team = '{statcast_code}'")
        else:
            clauses.append(f"home_team != '{statcast_code}'")

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


def _compute_split_groups(df, player_type: str, split_type: str,
                          team_statcast_code: str = 'AZ') -> list:
    """
    Given a filtered pitch DataFrame, group by split_type and compute stats.
    Imports compute functions lazily from process.py.
    """
    from process import compute_batter_stats, compute_pitcher_stats
    import pandas as pd

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
            add(str(int(v)), df[df["inning"] == v])

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
            add("Home", df[df["home_team"] == team_statcast_code])
            add("Away", df[df["home_team"] != team_statcast_code])
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

    elif split_type == "month":
        import calendar as _cal
        df = df.copy()
        df["_m"] = pd.to_datetime(df["game_date"], errors="coerce").dt.month
        for m in sorted(df["_m"].dropna().unique()):
            add(_cal.month_name[int(m)], df[df["_m"] == m])

    # Prepend overall (filtered) row
    overall = compute_fn(df)
    if overall:
        results.insert(0, {"split_type": split_type, "split_value": "All (filtered)", **overall})

    return results


def _get_player_team_statcast_code(player_id: int) -> str:
    """Look up a player's team and return its Statcast code."""
    rows = query("SELECT team FROM players WHERE player_id=?", (player_id,))
    if not rows:
        return 'AZ'
    team_code = rows[0]['team']
    return TEAMS.get(team_code, TEAMS['ARI'])['statcast_code']


# ---------------------------------------------------------------------------
# Routes — Pages
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    """Team picker — show all teams with data in DB."""
    available = query("SELECT DISTINCT team FROM players")
    teams_with_data = []
    for r in available:
        code = r['team']
        if code in TEAMS:
            teams_with_data.append({**TEAMS[code], 'code': code})
    teams_with_data.sort(key=lambda t: t['full_name'])
    return render_template("teams.html", teams=teams_with_data, all_teams=TEAMS, season=SEASON)


@app.route("/<team_code>")
def roster(team_code: str):
    """Team roster page."""
    team_code = team_code.upper()
    if team_code not in TEAMS:
        abort(404)

    team = {**TEAMS[team_code], 'code': team_code}

    def jersey_key(p):
        try:
            return int(p["jersey_number"] or 9999)
        except (ValueError, TypeError):
            return 9999

    batters = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number, p.position_type "
        "FROM players p "
        "WHERE p.position_type != 'Pitcher' AND p.season = ? AND p.team = ?",
        (SEASON, team_code)
    )
    pitchers = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number, p.position_type "
        "FROM players p "
        "WHERE p.position_type = 'Pitcher' AND p.season = ? AND p.team = ?",
        (SEASON, team_code)
    )

    # Classify pitchers as SP vs RP: starters pitched in inning 1 in 3+ games
    pitcher_ids = tuple(p["player_id"] for p in pitchers)
    if pitcher_ids:
        placeholders = ",".join("?" * len(pitcher_ids))
        starter_rows = query(
            f"SELECT pitcher FROM pitches "
            f"WHERE pitcher IN ({placeholders}) AND inning = 1 "
            f"GROUP BY pitcher HAVING COUNT(DISTINCT game_pk) >= 3",
            pitcher_ids
        )
        starter_ids = {r["pitcher"] for r in starter_rows}
    else:
        starter_ids = set()

    for p in pitchers:
        p["position"] = "SP" if p["player_id"] in starter_ids else "RP"

    batters.sort(key=jersey_key)
    pitchers.sort(key=jersey_key)
    all_players = sorted(batters + pitchers, key=jersey_key)

    return render_template("index.html", batters=batters, pitchers=pitchers,
                           all_players=all_players, team=team, season=SEASON)


@app.route("/batter/<int:player_id>")
def batter_page(player_id: int):
    player = query(
        "SELECT * FROM players WHERE player_id=? AND season=?", (player_id, SEASON)
    )
    if not player:
        abort(404)
    player = player[0]
    if player["position_type"] == "Pitcher":
        abort(404)

    team_code = player.get("team", "ARI")
    team = {**TEAMS.get(team_code, TEAMS['ARI']), 'code': team_code}

    overall = query(
        "SELECT * FROM batter_splits WHERE player_id=? AND season=? AND split_type='overall'",
        (player_id, SEASON)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM batter_splits "
        "WHERE player_id=? AND season=? ORDER BY split_type", (player_id, SEASON)
    )]
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE batter=? AND pitch_type IS NOT NULL "
        "ORDER BY pitch_type", (player_id,)
    )]
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="batter", team=team)


@app.route("/pitcher/<int:player_id>")
def pitcher_page(player_id: int):
    player = query(
        "SELECT * FROM players WHERE player_id=? AND season=?", (player_id, SEASON)
    )
    if not player:
        abort(404)
    player = player[0]
    if player["position_type"] != "Pitcher":
        abort(404)

    team_code = player.get("team", "ARI")
    team = {**TEAMS.get(team_code, TEAMS['ARI']), 'code': team_code}

    overall = query(
        "SELECT * FROM pitcher_splits WHERE player_id=? AND season=? AND split_type='overall'",
        (player_id, SEASON)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM pitcher_splits "
        "WHERE player_id=? AND season=? ORDER BY split_type", (player_id, SEASON)
    )]
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL "
        "ORDER BY pitch_type", (player_id,)
    )]
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="pitcher", team=team)


# ---------------------------------------------------------------------------
# API — Pre-computed splits (no active filters)
# ---------------------------------------------------------------------------

@app.route("/api/players")
def api_players():
    players = query(
        f"SELECT player_id, full_name, position, position_type, jersey_number "
        f"FROM players WHERE season={SEASON} ORDER BY position_type DESC, full_name"
    )
    return jsonify(players)


@app.route("/api/batter/<int:player_id>/<split_type>")
def api_batter_split(player_id: int, split_type: str):
    rows = query(
        f"SELECT * FROM batter_splits WHERE player_id=? AND season={SEASON} AND split_type=? "
        "ORDER BY split_value",
        (player_id, split_type)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/<split_type>")
def api_pitcher_split(player_id: int, split_type: str):
    rows = query(
        f"SELECT * FROM pitcher_splits WHERE player_id=? AND season={SEASON} AND split_type=? "
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
    statcast_code = _get_player_team_statcast_code(player_id)
    return jsonify(_compute_split_groups(df, "batter", split_type,
                                        team_statcast_code=statcast_code))


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
    statcast_code = _get_player_team_statcast_code(player_id)
    return jsonify(_compute_split_groups(df, "pitcher", split_type,
                                        team_statcast_code=statcast_code))


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
# Value — WAR, salary, awards
# ---------------------------------------------------------------------------

def _get_value_data(player_id: int):
    value = query(
        f"SELECT war, salary, dollars_per_war FROM player_value "
        f"WHERE player_id=? AND season={SEASON}", (player_id,)
    )
    awards = query(
        f"SELECT award_name FROM player_awards WHERE player_id=? AND season={SEASON}",
        (player_id,)
    )
    v = value[0] if value else {}
    return {
        "war": v.get("war"),
        "salary": v.get("salary"),
        "dollars_per_war": v.get("dollars_per_war"),
        "awards": [a["award_name"] for a in awards],
    }


@app.route("/api/batter/<int:player_id>/value")
def batter_value(player_id: int):
    return jsonify(_get_value_data(player_id))


@app.route("/api/pitcher/<int:player_id>/value")
def pitcher_value(player_id: int):
    return jsonify(_get_value_data(player_id))


# ---------------------------------------------------------------------------
# Defense — fielding stats + sprint speed
# ---------------------------------------------------------------------------

def _get_defense_data(player_id: int):
    row = query(
        "SELECT position, games, innings, errors, fielding_pct, drs, def_runs, oaa, "
        f"sprint_speed, sprint_pct FROM player_defense WHERE player_id=? AND season={SEASON}",
        (player_id,)
    )
    return dict(row[0]) if row else {}


@app.route("/api/batter/<int:player_id>/defense")
def batter_defense(player_id: int):
    return jsonify(_get_defense_data(player_id))


@app.route("/api/pitcher/<int:player_id>/defense")
def pitcher_defense(player_id: int):
    return jsonify(_get_defense_data(player_id))


# ---------------------------------------------------------------------------
# Team payroll pages
# ---------------------------------------------------------------------------

@app.route("/<team_code>/payroll")
def payroll(team_code: str):
    team_code = team_code.upper()
    if team_code not in TEAMS:
        abort(404)
    team = {**TEAMS[team_code], 'code': team_code}

    players = query(f"""
        SELECT p.player_id, p.full_name, p.position, p.position_type,
               p.jersey_number,
               v.war, v.salary, v.dollars_per_war
        FROM players p
        LEFT JOIN player_value v ON p.player_id = v.player_id
                                 AND v.season = {SEASON}
                                 AND v.team = p.team
        WHERE p.season = {SEASON} AND p.team = ?
        ORDER BY v.salary DESC
    """, (team_code,))
    total_salary = sum(p["salary"] for p in players if p["salary"])
    return render_template("payroll.html", players=players, total_salary=total_salary, team=team)


# Old /payroll URL → 301 redirect to /ari/payroll
@app.route("/payroll")
def payroll_redirect():
    return redirect("/ari/payroll", code=301)


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

@app.route("/sitemap.xml")
def sitemap():
    """Generate sitemap for all player pages + static pages."""
    base = "https://dugoutintel.com"
    players = query(
        f"SELECT player_id, position_type FROM players WHERE season = {SEASON}"
    )
    available_teams = query("SELECT DISTINCT team FROM players")
    urls = [base + "/", base + "/privacy"]
    for r in available_teams:
        code = r['team'].lower()
        urls.append(f"{base}/{code}")
        urls.append(f"{base}/{code}/payroll")
    for p in players:
        route = "pitcher" if p["position_type"] == "Pitcher" else "batter"
        urls.append(f"{base}/{route}/{p['player_id']}")
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        xml_lines.append(f"  <url><loc>{url}</loc></url>")
    xml_lines.append("</urlset>")
    return Response("\n".join(xml_lines), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"WARNING: Database not found at {DB_PATH}")
        print("Run: python src/load_db.py --migrate")
    app.run(debug=True, port=5000)
