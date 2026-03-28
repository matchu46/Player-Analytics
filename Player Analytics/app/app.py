"""
app.py — Flask web application for MLB team analytics.
"""

import gzip
import json
import os
import shutil
import sys
import sqlite3
import urllib.request

from flask import Flask, jsonify, render_template, abort, request, redirect, Response
from flask_caching import Cache

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
DB_PATH  = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "db", "baseball.db"))
DB_GZ     = os.path.join(DATA_DIR, "db", "baseball.db.gz")
DB_GZ_URL = "https://github.com/matchu46/Player-Analytics/releases/download/v1.0-db/baseball.db.gz"
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR   = os.path.join(BASE_DIR, "static")


def _is_valid_sqlite(path: str) -> bool:
    """Return True if path is a real SQLite database (not an LFS pointer)."""
    try:
        with open(path, "rb") as f:
            return f.read(16).startswith(b"SQLite format 3")
    except OSError:
        return False


def _ensure_db():
    """Seed DB from .gz if missing, invalid, or DB_VERSION has been bumped."""
    target_version  = os.environ.get("DB_VERSION", "1")
    version_file    = DB_PATH + ".version"

    current_version = ""
    if os.path.exists(version_file):
        with open(version_file) as f:
            current_version = f.read().strip()

    needs_seed = not _is_valid_sqlite(DB_PATH) or current_version != target_version

    if needs_seed:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        if not os.path.exists(DB_GZ):
            print(f"[startup] downloading DB from GitHub release…", flush=True)
            urllib.request.urlretrieve(DB_GZ_URL, DB_GZ)
            print(f"[startup] download complete.", flush=True)
        print(f"[startup] seeding DB (version {target_version}) from {DB_GZ} …", flush=True)
        with gzip.open(DB_GZ, "rb") as f_in, open(DB_PATH, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        with open(version_file, "w") as f:
            f.write(target_version)
        print("[startup] database ready.", flush=True)
    else:
        print(f"[startup] DB up to date (version {current_version}).", flush=True)

_ensure_db()


def _migrate_columns():
    """Non-destructively add new columns to existing DB on startup."""
    conn = sqlite3.connect(DB_PATH)
    b_cols = {r[1] for r in conn.execute("PRAGMA table_info(batter_splits)").fetchall()}
    p_cols = {r[1] for r in conn.execute("PRAGMA table_info(pitcher_splits)").fetchall()}
    for col in ['babip', 'ops_plus', 'wrc_plus']:
        if col not in b_cols:
            conn.execute(f"ALTER TABLE batter_splits ADD COLUMN {col} REAL")
    for col in ['fip', 'era_plus']:
        if col not in p_cols:
            conn.execute(f"ALTER TABLE pitcher_splits ADD COLUMN {col} REAL")
    conn.commit()
    conn.close()

_migrate_columns()

# Allow importing from src/
sys.path.insert(0, os.path.join(BASE_DIR, "..", "src"))

from teams import TEAMS, SEASON, SEASON_DATES

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
app.config["CACHE_TYPE"] = "SimpleCache"
app.config["CACHE_DEFAULT_TIMEOUT"] = 300  # 5 minutes
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # 1 year for static assets
cache = Cache(app)

STATIC_VERSION = "4"  # bump when CSS/JS changes to bust browser cache


@app.context_processor
def inject_static_version():
    return {"static_v": STATIC_VERSION}


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
        innings = [i.strip() for i in inning.split(",") if i.strip()]
        has_9plus = "9+" in innings
        numeric = [int(i) for i in innings if i != "9+"]
        if has_9plus and numeric:
            placeholders = ",".join("?" * len(numeric))
            clauses.append(f"(inning >= 9 OR inning IN ({placeholders}))")
            params.extend(numeric)
        elif has_9plus:
            clauses.append("inning >= 9")
        elif len(numeric) == 1:
            clauses.append("inning = ?")
            params.append(numeric[0])
        elif numeric:
            placeholders = ",".join("?" * len(numeric))
            clauses.append(f"inning IN ({placeholders})")
            params.extend(numeric)

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

    opponent_id = request.args.get("opponent_id")
    if opponent_id:
        opposite_col = "pitcher" if player_col == "batter" else "batter"
        clauses.append(f"{opposite_col} = ?")
        params.append(int(opponent_id))

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
        types = [t.strip() for t in pitch_type.split(",") if t.strip()]
        if len(types) == 1:
            clauses.append("pitch_type = ?")
            params.append(types[0])
        elif len(types) > 1:
            placeholders = ",".join("?" * len(types))
            clauses.append(f"pitch_type IN ({placeholders})")
            params.extend(types)

    stadium = request.args.get("stadium")
    if stadium:
        clauses.append("home_team = ?")
        params.append(stadium)

    season = request.args.get("season", type=int)
    if season and season in SEASON_DATES:
        dates = SEASON_DATES[season]
        clauses.append("game_date BETWEEN ? AND ?")
        params.extend([dates["season_start"], dates["season_end"]])

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

    elif split_type in ("opponent_team", "opponent_division", "opponent_league"):
        from teams import TEAMS as _TEAMS
        _SC_NAME = {cfg['statcast_code']: cfg['full_name'] for cfg in _TEAMS.values()}
        _SC_DIV  = {cfg['statcast_code']: cfg.get('division', '') for cfg in _TEAMS.values()}
        _SC_LEA  = {cfg['statcast_code']: cfg.get('league', '') for cfg in _TEAMS.values()}

        df = df.copy()
        if player_type == "pitcher":
            df["_opp"] = df.apply(
                lambda r: r["away_team"] if r["home_team"] == team_statcast_code else r["home_team"],
                axis=1,
            )
        else:
            df["_opp"] = df.apply(
                lambda r: r["away_team"] if r.get("inning_topbot") == "Bot" else r["home_team"],
                axis=1,
            )

        if split_type == "opponent_team":
            for opp_code in sorted(df["_opp"].dropna().unique()):
                name = _SC_NAME.get(opp_code, opp_code)
                add(name, df[df["_opp"] == opp_code])
        elif split_type == "opponent_division":
            for div in sorted({v for v in _SC_DIV.values() if v}):
                div_codes = {k for k, v in _SC_DIV.items() if v == div}
                add(div, df[df["_opp"].isin(div_codes)])
        else:  # opponent_league
            for league in ["AL", "NL"]:
                league_codes = {k for k, v in _SC_LEA.items() if v == league}
                add(league, df[df["_opp"].isin(league_codes)])

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

_DIVISION_ORDER = [
    "NL West", "NL Central", "NL East",
    "AL West", "AL Central", "AL East",
]

@app.route("/")
@cache.cached(timeout=300)
def home():
    """Team picker — show all teams grouped by division."""
    available = query("SELECT DISTINCT team FROM players")
    active_codes = {r['team'] for r in available}

    divisions = {div: [] for div in _DIVISION_ORDER}
    for code, cfg in TEAMS.items():
        div = cfg.get('division', 'Other')
        if div in divisions:
            divisions[div].append({
                **cfg,
                'code': code,
                'has_data': code in active_codes,
            })
    for div in divisions:
        divisions[div].sort(key=lambda t: t['full_name'])

    return render_template("teams.html", divisions=divisions,
                           division_order=_DIVISION_ORDER, season=SEASON)


@app.route("/<team_code>")
@cache.cached(timeout=300, query_string=True)
def roster(team_code: str):
    """Team roster page."""
    team_code = team_code.upper()
    if team_code not in TEAMS:
        abort(404)

    team = {**TEAMS[team_code], 'code': team_code}

    available_seasons = [r["season"] for r in query(
        "SELECT DISTINCT season FROM players WHERE team=? ORDER BY season DESC", (team_code,)
    )]
    season = request.args.get("season", SEASON, type=int)
    if season not in available_seasons:
        season = available_seasons[0] if available_seasons else SEASON

    def jersey_key(p):
        try:
            return int(float(p["jersey_number"] or 9999))
        except (ValueError, TypeError):
            return 9999

    batters = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number, p.position_type "
        "FROM players p "
        "WHERE p.position_type != 'Pitcher' AND p.season = ? AND p.team = ?",
        (season, team_code)
    )
    pitchers = query(
        "SELECT p.player_id, p.full_name, p.position, p.jersey_number, p.position_type "
        "FROM players p "
        "WHERE p.position_type = 'Pitcher' AND p.season = ? AND p.team = ?",
        (season, team_code)
    )

    # Two-way players (e.g. Ohtani) appear in batters; also add them to pitchers tab
    twp = [dict(p) for p in batters if p["position_type"] == "Two-Way Player"]
    pitchers_for_tab = pitchers + twp

    # Classify pitchers as SP vs RP: starters pitched in inning 1 in 3+ games
    pitcher_ids = tuple(p["player_id"] for p in pitchers_for_tab)
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

    for p in pitchers_for_tab:
        p["position"] = "SP" if p["player_id"] in starter_ids else "RP"

    batters.sort(key=jersey_key)
    pitchers_for_tab.sort(key=jersey_key)
    # all_players: batters (includes TWP) + pure pitchers — no duplicates
    pure_pitchers = [p for p in pitchers_for_tab if p["position_type"] == "Pitcher"]
    all_players = sorted(batters + pure_pitchers, key=jersey_key)

    return render_template("index.html", batters=batters, pitchers=pitchers_for_tab,
                           all_players=all_players, team=team, season=season,
                           available_seasons=available_seasons)


@app.route("/batter/<int:player_id>")
def batter_page(player_id: int):
    available_seasons = [r["season"] for r in query(
        "SELECT DISTINCT season FROM players WHERE player_id=? ORDER BY season DESC", (player_id,)
    )]
    season = request.args.get("season", SEASON, type=int)
    if season not in available_seasons:
        season = available_seasons[0] if available_seasons else SEASON

    player = query(
        "SELECT * FROM players WHERE player_id=? AND season=?", (player_id, season)
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
        (player_id, season)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM batter_splits "
        "WHERE player_id=? AND season=? ORDER BY split_type", (player_id, season)
    )]
    dates = SEASON_DATES.get(season, SEASON_DATES[SEASON])
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE batter=? AND pitch_type IS NOT NULL "
        "AND game_date BETWEEN ? AND ? ORDER BY pitch_type",
        (player_id, dates["season_start"], dates["season_end"])
    )]
    statcast_code = TEAMS.get(team_code, TEAMS['ARI'])['statcast_code']
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="batter", team=team,
                           season=season, available_seasons=available_seasons,
                           home_park=statcast_code)


@app.route("/pitcher/<int:player_id>")
def pitcher_page(player_id: int):
    available_seasons = [r["season"] for r in query(
        "SELECT DISTINCT season FROM players WHERE player_id=? ORDER BY season DESC", (player_id,)
    )]
    season = request.args.get("season", SEASON, type=int)
    if season not in available_seasons:
        season = available_seasons[0] if available_seasons else SEASON

    player = query(
        "SELECT * FROM players WHERE player_id=? AND season=?", (player_id, season)
    )
    if not player:
        abort(404)
    player = player[0]
    if player["position_type"] not in ("Pitcher", "Two-Way Player"):
        abort(404)

    team_code = player.get("team", "ARI")
    team = {**TEAMS.get(team_code, TEAMS['ARI']), 'code': team_code}

    overall = query(
        "SELECT * FROM pitcher_splits WHERE player_id=? AND season=? AND split_type='overall'",
        (player_id, season)
    )
    overall = overall[0] if overall else {}
    split_types = [r["split_type"] for r in query(
        "SELECT DISTINCT split_type FROM pitcher_splits "
        "WHERE player_id=? AND season=? ORDER BY split_type", (player_id, season)
    )]
    dates = SEASON_DATES.get(season, SEASON_DATES[SEASON])
    pitch_types = [r["pitch_type"] for r in query(
        "SELECT DISTINCT pitch_type FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL "
        "AND game_date BETWEEN ? AND ? ORDER BY pitch_type",
        (player_id, dates["season_start"], dates["season_end"])
    )]
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="pitcher", team=team,
                           season=season, available_seasons=available_seasons,
                           home_park='AZ')


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
    season = request.args.get("season", SEASON, type=int)
    rows = query(
        "SELECT * FROM batter_splits WHERE player_id=? AND season=? AND split_type=? "
        "ORDER BY split_value",
        (player_id, season, split_type)
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/<split_type>")
def api_pitcher_split(player_id: int, split_type: str):
    season = request.args.get("season", SEASON, type=int)
    rows = query(
        "SELECT * FROM pitcher_splits WHERE player_id=? AND season=? AND split_type=? "
        "ORDER BY split_value",
        (player_id, season, split_type)
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
    season = request.args.get("season", SEASON, type=int)
    dates  = SEASON_DATES.get(season, SEASON_DATES[SEASON])
    rows = query(
        "SELECT pitch_type, pfx_x, pfx_z, release_speed, release_spin_rate "
        "FROM pitches WHERE pitcher=? "
        "AND pfx_x IS NOT NULL AND pfx_z IS NOT NULL AND pitch_type IS NOT NULL "
        "AND game_date BETWEEN ? AND ? LIMIT 8000",
        (player_id, dates["season_start"], dates["season_end"])
    )
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/velocity")
def api_pitcher_velocity(player_id: int):
    season = request.args.get("season", SEASON, type=int)
    dates  = SEASON_DATES.get(season, SEASON_DATES[SEASON])
    rows = query(
        "SELECT pitch_type, COUNT(*) as pitches, "
        "AVG(release_speed) as avg_velo, MAX(release_speed) as max_velo, "
        "MIN(release_speed) as min_velo, AVG(release_spin_rate) as avg_spin "
        "FROM pitches WHERE pitcher=? AND pitch_type IS NOT NULL "
        "AND game_date BETWEEN ? AND ? "
        "GROUP BY pitch_type ORDER BY pitches DESC",
        (player_id, dates["season_start"], dates["season_end"])
    )
    total = sum(r["pitches"] for r in rows)
    for r in rows:
        r["usage_pct"] = round(r["pitches"] / total * 100, 1) if total > 0 else 0
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Player search (proxied to MLB Stats API)
# ---------------------------------------------------------------------------

@app.route("/api/search/players")
def search_players():
    import requests as _req
    q = request.args.get("q", "").strip()
    position_type = request.args.get("type", "").lower()  # "pitcher" or "batter"
    if not q or len(q) < 2:
        return jsonify([])
    try:
        resp = _req.get(
            "https://statsapi.mlb.com/api/v1/people/search",
            params={"names": q, "sportId": 1},
            timeout=5,
        )
        people = resp.json().get("people", [])
        if position_type == "pitcher":
            people = [p for p in people
                      if p.get("primaryPosition", {}).get("type") == "Pitcher"]
        elif position_type == "batter":
            people = [p for p in people
                      if p.get("primaryPosition", {}).get("type") != "Pitcher"]
        return jsonify([{"id": p["id"], "name": p.get("fullName", "")} for p in people[:12]])
    except Exception:
        return jsonify([])


# ---------------------------------------------------------------------------
# API — Global player search (searches our DB)
# ---------------------------------------------------------------------------

@app.route("/api/search")
@cache.cached(timeout=600, query_string=True)
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    rows = query(
        """SELECT player_id, full_name, team, position, position_type
           FROM players
           WHERE full_name LIKE ? AND season = ?
           ORDER BY full_name
           LIMIT 15""",
        (f"%{q}%", SEASON)
    )
    results = []
    for r in rows:
        player_type = "pitcher" if r["position"] == "P" else "batter"
        results.append({
            "id":       r["player_id"],
            "name":     r["full_name"],
            "team":     r["team"],
            "position": r["position"],
            "type":     player_type,
            "url":      f"/{player_type}/{r['player_id']}",
        })
    return jsonify(results)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@app.route("/ads.txt")
def ads_txt():
    return Response(
        "google.com, pub-6203828588530023, DIRECT, f08c47fec0942fa0\n",
        mimetype="text/plain"
    )


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/glossary")
def glossary():
    return render_template("glossary.html")


@app.route("/leaderboards")
@cache.cached(timeout=300, query_string=True)
def leaderboards():
    season = request.args.get("season", SEASON, type=int)
    min_pa = request.args.get("min_pa", 25, type=int)
    min_ip = request.args.get("min_ip", 5, type=float)

    batters = query(
        """
        SELECT b.player_id, p.full_name, p.team, p.position,
               b.pa, b.ab, b.hits, b.singles, b.doubles, b.triples,
               b.home_runs, b.rbi, b.walks, b.strikeouts, b.hbp,
               b.avg, b.obp, b.slg, b.ops, b.woba,
               b.avg_exit_velo, b.avg_launch_angle,
               b.hard_hit_pct, b.barrel_pct,
               b.swing_pct, b.whiff_pct, b.contact_pct,
               b.babip, b.ops_plus, b.wrc_plus,
               CASE WHEN b.pa > 0 THEN CAST(b.strikeouts AS REAL)/b.pa ELSE NULL END AS k_pct,
               CASE WHEN b.pa > 0 THEN CAST(b.walks AS REAL)/b.pa ELSE NULL END AS bb_pct
        FROM batter_splits b
        JOIN players p ON b.player_id = p.player_id AND b.season = p.season
        WHERE b.season = ? AND b.split_type = 'overall' AND b.pa >= ?
        ORDER BY b.woba DESC
        """,
        (season, min_pa)
    )

    pitchers = query(
        """
        SELECT b.player_id, p.full_name, p.team, p.position,
               b.era, b.whip, b.innings_pitched, b.batters_faced,
               b.strikeouts, b.walks_allowed, b.hits_allowed, b.hbp,
               b.home_runs_allowed,
               b.k_pct, b.bb_pct, b.k_bb,
               b.avg_against, b.obp_against, b.slg_against, b.woba_against,
               b.avg_velo, b.avg_spin_rate,
               b.fip, b.era_plus
        FROM pitcher_splits b
        JOIN players p ON b.player_id = p.player_id AND b.season = p.season
        WHERE b.season = ? AND b.split_type = 'overall'
              AND b.innings_pitched >= ?
        ORDER BY b.era ASC
        """,
        (season, min_ip)
    )

    available_seasons = [r["season"] for r in query(
        "SELECT DISTINCT season FROM batter_splits ORDER BY season DESC"
    )]

    return render_template(
        "leaderboards.html",
        batters=batters,
        pitchers=pitchers,
        season=season,
        available_seasons=available_seasons,
        min_pa=min_pa,
        min_ip=min_ip,
    )




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
    urls = [base + "/", base + "/leaderboards", base + "/about", base + "/glossary", base + "/privacy"]
    for r in available_teams:
        code = r['team'].lower()
        urls.append(f"{base}/{code}")
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


# ---------------------------------------------------------------------------
# Scheduler — daily incremental update (enabled via SCHEDULER_ENABLED=1)
# ---------------------------------------------------------------------------

def _run_daily_update():
    """Run update_all.py in a subprocess — fetches last 3 days for all teams."""
    import subprocess
    src_dir = os.path.join(BASE_DIR, "..", "src")
    script  = os.path.join(src_dir, "update_all.py")
    print("[scheduler] Starting daily update...", flush=True)
    try:
        subprocess.run(
            [sys.executable, script, "--days", "3"],
            check=True,
            timeout=10_800,   # 3-hour hard cap
        )
        print("[scheduler] Daily update complete.", flush=True)
    except Exception as e:
        print(f"[scheduler] Daily update failed: {e}", flush=True)


if os.environ.get("SCHEDULER_ENABLED") == "1":
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler(timezone="America/New_York")
    _scheduler.add_job(_run_daily_update, "cron", hour=6, minute=0)
    _scheduler.start()
    print("[scheduler] Daily update scheduled at 06:00 ET.", flush=True)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"WARNING: Database not found at {DB_PATH}")
        print("Run: python src/load_db.py --migrate")
    app.run(debug=True, port=5000)
