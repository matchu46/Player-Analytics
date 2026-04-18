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
DB_GZ     = os.path.join(DATA_DIR, "db", "baseball_prod.db.gz")
DB_GZ_URL = "https://github.com/matchu46/Player-Analytics/releases/download/db-prod-v2/baseball_prod.db.gz"
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
    for col in ['fip', 'era_plus', 'whiff_pct']:
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
app.config["CACHE_THRESHOLD"] = 100        # max cached items before eviction
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # 1 year for static assets
cache = Cache(app)

STATIC_VERSION = "7"  # bump when CSS/JS changes to bust browser cache
PITCH_DATA_MIN_SEASON = 2022  # raw pitches only stored for 2022+ in prod DB


@app.context_processor
def inject_static_version():
    return {"static_v": STATIC_VERSION}


@app.before_request
def force_https():
    # Railway sets X-Forwarded-Proto header; redirect http -> https in production
    if request.headers.get("X-Forwarded-Proto") == "http":
        return redirect(request.url.replace("http://", "https://", 1), code=301)
    # Redirect www → non-www
    if request.host.startswith("www."):
        return redirect("https://dugoutintel.com" + request.full_path.rstrip("?"), 301)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA cache_size = -2000")   # 2MB page cache per connection
    conn.execute("PRAGMA temp_store = MEMORY")  # temp tables in memory (faster)
    conn.execute("PRAGMA mmap_size = 0")        # disable memory-mapped I/O
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


@app.route("/<team_code>/stats")
@cache.cached(timeout=300, query_string=True)
def team_stats_page(team_code: str):
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

    _ip = ("CAST(innings_pitched AS INTEGER)"
           " + (innings_pitched - CAST(innings_pitched AS INTEGER)) * 10.0 / 3.0")

    batting = query(f"""
        SELECT SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as hits,
            SUM(home_runs) as hr, SUM(walks) as walks, SUM(strikeouts) as strikeouts,
            CASE WHEN SUM(ab)>0 THEN ROUND(CAST(SUM(hits) AS REAL)/SUM(ab),3) END as avg,
            CASE WHEN SUM(CASE WHEN obp IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN obp IS NOT NULL THEN obp*pa ELSE 0 END)
                    /SUM(CASE WHEN obp IS NOT NULL THEN pa ELSE 0 END),3) END as obp,
            CASE WHEN SUM(CASE WHEN slg IS NOT NULL THEN ab ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN slg IS NOT NULL THEN slg*ab ELSE 0 END)
                    /SUM(CASE WHEN slg IS NOT NULL THEN ab ELSE 0 END),3) END as slg,
            CASE WHEN SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN woba IS NOT NULL THEN woba*pa ELSE 0 END)
                    /SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END),3) END as woba,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(strikeouts) AS REAL)/SUM(pa)*100,1) END as k_pct,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(walks) AS REAL)/SUM(pa)*100,1) END as bb_pct,
            CASE WHEN SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN wrc_plus IS NOT NULL THEN wrc_plus*pa ELSE 0 END)
                    /SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)) END as wrc_plus,
            CASE WHEN SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN hard_hit_pct*pa ELSE 0 END)
                    /SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END)*100,1) END as hard_hit_pct,
            CASE WHEN SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN barrel_pct IS NOT NULL THEN barrel_pct*pa ELSE 0 END)
                    /SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END)*100,1) END as barrel_pct,
            CASE WHEN SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN avg_exit_velo*pa ELSE 0 END)
                    /SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END),1) END as avg_exit_velo
        FROM batter_splits
        WHERE team=? AND season=? AND split_type='overall' AND split_value='All'
    """, (team_code, season))

    pitching = query(f"""
        SELECT SUM(batters_faced) as bf,
            ROUND(SUM({_ip}), 1) as ip,
            SUM(strikeouts) as strikeouts, SUM(walks_allowed) as walks,
            SUM(home_runs_allowed) as hr,
            CASE WHEN SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN era IS NOT NULL THEN era*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as era,
            CASE WHEN SUM(CASE WHEN fip IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN fip IS NOT NULL THEN fip*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN fip IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as fip,
            CASE WHEN SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN whip IS NOT NULL THEN whip*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as whip,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(strikeouts) AS REAL)/SUM(batters_faced)*100,1) END as k_pct,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(walks_allowed) AS REAL)/SUM(batters_faced)*100,1) END as bb_pct,
            CASE WHEN SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN woba_against IS NOT NULL THEN woba_against*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as woba_against,
            CASE WHEN SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN avg_velo IS NOT NULL THEN avg_velo*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END),1) END as avg_velo
        FROM pitcher_splits
        WHERE team=? AND season=? AND split_type='overall' AND split_value='All'
    """, (team_code, season))

    batting  = batting[0]  if batting  else {}
    pitching = pitching[0] if pitching else {}
    if batting.get('obp') and batting.get('slg'):
        batting['ops'] = round(batting['obp'] + batting['slg'], 3)

    return render_template("team_stats.html", team=team, season=season,
                           available_seasons=available_seasons,
                           batting=batting, pitching=pitching)


import bisect

def _pct_rank(sorted_vals: list, player_val, lower_is_better: bool):
    if player_val is None or not sorted_vals:
        return None
    rank = bisect.bisect_left(sorted_vals, player_val)
    pct  = rank / len(sorted_vals) * 100
    return round(100 - pct if lower_is_better else pct)

def _compute_percentiles(player_id: int, season: int, player_type: str, overall: dict) -> list:
    if player_type == 'batter':
        all_rows = query("""
            SELECT player_id, woba, avg_exit_velo, hard_hit_pct, barrel_pct,
                   babip, wrc_plus,
                   CASE WHEN pa>0 THEN CAST(strikeouts AS REAL)/pa END as k_pct,
                   CASE WHEN pa>0 THEN CAST(walks AS REAL)/pa END as bb_pct
            FROM batter_splits
            WHERE season=? AND split_type='overall' AND split_value='All' AND pa>=50
        """, (season,))
        pool = {r['player_id']: r for r in all_rows}
        me   = pool.get(player_id, {})
        me_k  = (overall.get('strikeouts') or 0) / (overall.get('pa') or 1)
        me_bb = (overall.get('walks')      or 0) / (overall.get('pa') or 1)
        metrics = [
            ('wRC+',       'wrc_plus',      me.get('wrc_plus'),     False),
            ('wOBA',       'woba',          me.get('woba'),         False),
            ('Exit Velo',  'avg_exit_velo', me.get('avg_exit_velo'),False),
            ('Hard Hit%',  'hard_hit_pct',  me.get('hard_hit_pct'), False),
            ('Barrel%',    'barrel_pct',    me.get('barrel_pct'),   False),
            ('K%',         'k_pct',         me_k,                   True),
            ('BB%',        'bb_pct',        me_bb,                  False),
            ('BABIP',      'babip',         me.get('babip'),        False),
        ]
        field_map = {m[1]: m for m in metrics}
    else:
        all_rows = query("""
            SELECT player_id, era, fip, k_pct, bb_pct, avg_velo,
                   woba_against, whip, avg_spin_rate
            FROM pitcher_splits
            WHERE season=? AND split_type='overall' AND split_value='All' AND batters_faced>=30
        """, (season,))
        pool = {r['player_id']: r for r in all_rows}
        me   = pool.get(player_id, {})
        metrics = [
            ('ERA',        'era',           me.get('era'),           True),
            ('FIP',        'fip',           me.get('fip'),           True),
            ('WHIP',       'whip',          me.get('whip'),          True),
            ('K%',         'k_pct',         me.get('k_pct'),         False),
            ('BB%',        'bb_pct',        me.get('bb_pct'),        True),
            ('Avg Velo',   'avg_velo',      me.get('avg_velo'),      False),
            ('wOBA vs',    'woba_against',  me.get('woba_against'),  True),
            ('Spin Rate',  'avg_spin_rate', me.get('avg_spin_rate'), False),
        ]
        field_map = {m[1]: m for m in metrics}

    rings = []
    circumference = 188.4
    for label, field, player_val, lower_is_better in metrics:
        vals = sorted(r[field] for r in all_rows if r.get(field) is not None)
        pct  = _pct_rank(vals, player_val, lower_is_better)
        if pct is None:
            rings.append({'label': label, 'pct': None, 'color': '#555',
                          'arc': 0, 'gap': circumference, 'circ': circumference})
        else:
            if   pct >= 67: color = '#5cd45c'
            elif pct >= 33: color = '#f0c040'
            else:           color = '#e05c5c'
            arc = round(pct / 100 * circumference, 1)
            rings.append({'label': label, 'pct': pct, 'color': color,
                          'arc': arc, 'gap': round(circumference - arc, 1), 'circ': circumference})
    return rings


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
    percentiles = _compute_percentiles(player_id, season, 'batter', overall)
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="batter", team=team,
                           season=season, available_seasons=available_seasons,
                           home_park=statcast_code, percentiles=percentiles,
                           pitch_data_min_season=PITCH_DATA_MIN_SEASON)


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
    percentiles = _compute_percentiles(player_id, season, 'pitcher', overall)
    return render_template("player.html", player=player, overall=overall,
                           split_types=split_types, pitch_types=pitch_types,
                           player_type="pitcher", team=team,
                           season=season, available_seasons=available_seasons,
                           pitch_data_min_season=PITCH_DATA_MIN_SEASON,
                           home_park='AZ', percentiles=percentiles)


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
    # GROUP BY split_value so traded players (two team rows) collapse into one combined row.
    # Counting stats are summed; rate stats are re-derived from sums or PA-weighted.
    rows = query("""
        SELECT player_id, season, split_type, split_value,
            SUM(pa) as pa, SUM(ab) as ab, SUM(hits) as hits,
            SUM(singles) as singles, SUM(doubles) as doubles,
            SUM(triples) as triples, SUM(home_runs) as home_runs,
            SUM(rbi) as rbi, SUM(walks) as walks,
            SUM(strikeouts) as strikeouts, SUM(hbp) as hbp,
            CASE WHEN SUM(ab)>0 THEN ROUND(CAST(SUM(hits) AS REAL)/SUM(ab),3) END as avg,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(hits)+SUM(walks)+SUM(hbp) AS REAL)/SUM(pa),3) END as obp,
            CASE WHEN SUM(ab)>0 THEN ROUND(CAST(SUM(singles)+2*SUM(doubles)+3*SUM(triples)+4*SUM(home_runs) AS REAL)/SUM(ab),3) END as slg,
            CASE WHEN SUM(pa)>0 AND SUM(ab)>0 THEN ROUND(
                CAST(SUM(hits)+SUM(walks)+SUM(hbp) AS REAL)/SUM(pa) +
                CAST(SUM(singles)+2*SUM(doubles)+3*SUM(triples)+4*SUM(home_runs) AS REAL)/SUM(ab),3) END as ops,
            CASE WHEN SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN woba IS NOT NULL THEN woba*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END),3) END as woba,
            CASE WHEN SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN avg_exit_velo*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END),1) END as avg_exit_velo,
            CASE WHEN SUM(CASE WHEN avg_launch_angle IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_launch_angle IS NOT NULL THEN avg_launch_angle*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_launch_angle IS NOT NULL THEN pa ELSE 0 END),1) END as avg_launch_angle,
            CASE WHEN SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN hard_hit_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END),3) END as hard_hit_pct,
            CASE WHEN SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN barrel_pct IS NOT NULL THEN barrel_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END),3) END as barrel_pct,
            CASE WHEN SUM(CASE WHEN swing_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN swing_pct IS NOT NULL THEN swing_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN swing_pct IS NOT NULL THEN pa ELSE 0 END),3) END as swing_pct,
            CASE WHEN SUM(CASE WHEN whiff_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN whiff_pct IS NOT NULL THEN whiff_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN whiff_pct IS NOT NULL THEN pa ELSE 0 END),3) END as whiff_pct,
            CASE WHEN SUM(CASE WHEN contact_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN contact_pct IS NOT NULL THEN contact_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN contact_pct IS NOT NULL THEN pa ELSE 0 END),3) END as contact_pct,
            CASE WHEN SUM(ab)-SUM(strikeouts)-SUM(home_runs)>0
                THEN ROUND(CAST(SUM(hits)-SUM(home_runs) AS REAL)/(SUM(ab)-SUM(strikeouts)-SUM(home_runs)),3) END as babip,
            CASE WHEN SUM(CASE WHEN ops_plus IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN ops_plus IS NOT NULL THEN ops_plus*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN ops_plus IS NOT NULL THEN pa ELSE 0 END)) END as ops_plus,
            CASE WHEN SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN wrc_plus IS NOT NULL THEN wrc_plus*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)) END as wrc_plus
        FROM batter_splits
        WHERE player_id=? AND season=? AND split_type=?
        GROUP BY split_value ORDER BY split_value
    """, (player_id, season, split_type))
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/<split_type>")
def api_pitcher_split(player_id: int, split_type: str):
    season = request.args.get("season", SEASON, type=int)
    # GROUP BY split_value to collapse traded players into one combined row.
    # IP is in baseball convention (6.2 = 6⅔); convert to decimal for summing.
    _ip = ("CAST(innings_pitched AS INTEGER)"
           " + (innings_pitched - CAST(innings_pitched AS INTEGER)) * 10.0 / 3.0")
    rows = query(f"""
        SELECT player_id, season, split_type, split_value,
            SUM(batters_faced) as batters_faced,
            SUM(hits_allowed) as hits_allowed, SUM(runs_allowed) as runs_allowed,
            SUM(earned_runs) as earned_runs, SUM(home_runs_allowed) as home_runs_allowed,
            SUM(walks_allowed) as walks_allowed, SUM(strikeouts) as strikeouts, SUM(hbp) as hbp,
            ROUND(SUM({_ip}), 1) as innings_pitched,
            CASE WHEN SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN era IS NOT NULL THEN era*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as era,
            CASE WHEN SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN whip IS NOT NULL THEN whip*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as whip,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(strikeouts) AS REAL)/SUM(batters_faced),3) END as k_pct,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(walks_allowed) AS REAL)/SUM(batters_faced),3) END as bb_pct,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(strikeouts)-SUM(walks_allowed) AS REAL)/SUM(batters_faced),3) END as k_bb,
            CASE WHEN SUM(batters_faced)-SUM(walks_allowed)-SUM(hbp)>0
                THEN ROUND(CAST(SUM(hits_allowed) AS REAL)/(SUM(batters_faced)-SUM(walks_allowed)-SUM(hbp)),3) END as avg_against,
            CASE WHEN SUM(batters_faced)>0
                THEN ROUND(CAST(SUM(hits_allowed)+SUM(walks_allowed)+SUM(hbp) AS REAL)/SUM(batters_faced),3) END as obp_against,
            CASE WHEN SUM(CASE WHEN slg_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN slg_against IS NOT NULL THEN slg_against*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN slg_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as slg_against,
            CASE WHEN SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN woba_against IS NOT NULL THEN woba_against*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as woba_against,
            CASE WHEN SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_velo IS NOT NULL THEN avg_velo*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END),1) END as avg_velo,
            CASE WHEN SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN avg_spin_rate*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)) END as avg_spin_rate,
            CASE WHEN SUM(CASE WHEN fip IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN fip IS NOT NULL THEN fip*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN fip IS NOT NULL THEN batters_faced ELSE 0 END),2) END as fip,
            CASE WHEN SUM(CASE WHEN era_plus IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN era_plus IS NOT NULL THEN era_plus*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN era_plus IS NOT NULL THEN batters_faced ELSE 0 END)) END as era_plus,
            NULL as pitch_mix
        FROM pitcher_splits
        WHERE player_id=? AND season=? AND split_type=?
        GROUP BY split_value ORDER BY split_value
    """, (player_id, season, split_type))
    for r in rows:
        if r.get("pitch_mix"):
            try:
                r["pitch_mix"] = json.loads(r["pitch_mix"])
            except Exception:
                pass
    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — Trends (season-over-season stats)
# ---------------------------------------------------------------------------

@app.route("/api/batter/<int:player_id>/trends")
def api_batter_trends(player_id: int):
    rows = query("""
        SELECT season,
            SUM(pa) as pa, SUM(ab) as ab,
            SUM(hits) as hits, SUM(home_runs) as home_runs,
            SUM(strikeouts) as strikeouts, SUM(walks) as walks,
            CASE WHEN SUM(ab)>0 THEN ROUND(CAST(SUM(hits) AS REAL)/SUM(ab),3) END as avg,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(hits)+SUM(walks)+SUM(hbp) AS REAL)/SUM(pa),3) END as obp,
            CASE WHEN SUM(CASE WHEN slg IS NOT NULL THEN ab ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN slg IS NOT NULL THEN slg*ab ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN slg IS NOT NULL THEN ab ELSE 0 END),3) END as slg,
            CASE WHEN SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN woba IS NOT NULL THEN woba*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN woba IS NOT NULL THEN pa ELSE 0 END),3) END as woba,
            CASE WHEN SUM(ab)-SUM(strikeouts)-SUM(home_runs)>0
                THEN ROUND(CAST(SUM(hits)-SUM(home_runs) AS REAL)/(SUM(ab)-SUM(strikeouts)-SUM(home_runs)),3) END as babip,
            CASE WHEN SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN wrc_plus IS NOT NULL THEN wrc_plus*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN wrc_plus IS NOT NULL THEN pa ELSE 0 END)) END as wrc_plus,
            CASE WHEN SUM(CASE WHEN ops_plus IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN ops_plus IS NOT NULL THEN ops_plus*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN ops_plus IS NOT NULL THEN pa ELSE 0 END)) END as ops_plus,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(strikeouts) AS REAL)/SUM(pa),3) END as k_pct,
            CASE WHEN SUM(pa)>0 THEN ROUND(CAST(SUM(walks) AS REAL)/SUM(pa),3) END as bb_pct,
            CASE WHEN SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN avg_exit_velo*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_exit_velo IS NOT NULL THEN pa ELSE 0 END),1) END as avg_exit_velo,
            CASE WHEN SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN hard_hit_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN hard_hit_pct IS NOT NULL THEN pa ELSE 0 END),3) END as hard_hit_pct,
            CASE WHEN SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN barrel_pct IS NOT NULL THEN barrel_pct*pa ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN barrel_pct IS NOT NULL THEN pa ELSE 0 END),3) END as barrel_pct
        FROM batter_splits
        WHERE player_id=? AND split_type='overall' AND split_value='All'
        GROUP BY season ORDER BY season
    """, [player_id])
    # also compute ops
    for r in rows:
        if r.get('obp') is not None and r.get('slg') is not None:
            r['ops'] = round(r['obp'] + r['slg'], 3)
        else:
            r['ops'] = None
    return jsonify(rows)


@app.route("/api/pitcher/<int:player_id>/trends")
def api_pitcher_trends(player_id: int):
    _ip = ("CAST(innings_pitched AS INTEGER)"
           " + (innings_pitched - CAST(innings_pitched AS INTEGER)) * 10.0 / 3.0")
    overall = query(f"""
        SELECT season, SUM(batters_faced) as batters_faced,
            ROUND(SUM({_ip}), 1) as innings_pitched,
            SUM(strikeouts) as strikeouts, SUM(walks_allowed) as walks_allowed,
            CASE WHEN SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN era IS NOT NULL THEN era*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN era IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as era,
            CASE WHEN SUM(CASE WHEN fip IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN fip IS NOT NULL THEN fip*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN fip IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as fip,
            CASE WHEN SUM(CASE WHEN era_plus IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN era_plus IS NOT NULL THEN era_plus*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN era_plus IS NOT NULL THEN ({_ip}) ELSE 0 END)) END as era_plus,
            CASE WHEN SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN whip IS NOT NULL THEN whip*({_ip}) ELSE 0 END)
                    /SUM(CASE WHEN whip IS NOT NULL THEN ({_ip}) ELSE 0 END),2) END as whip,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(strikeouts) AS REAL)/SUM(batters_faced),3) END as k_pct,
            CASE WHEN SUM(batters_faced)>0 THEN ROUND(CAST(SUM(walks_allowed) AS REAL)/SUM(batters_faced),3) END as bb_pct,
            CASE WHEN SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_velo IS NOT NULL THEN avg_velo*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END),1) END as avg_velo,
            CASE WHEN SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN avg_spin_rate*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)) END as avg_spin_rate,
            CASE WHEN SUM(CASE WHEN avg_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_against IS NOT NULL THEN avg_against*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as avg_against,
            CASE WHEN SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN woba_against IS NOT NULL THEN woba_against*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as woba_against
        FROM pitcher_splits
        WHERE player_id=? AND split_type='overall' AND split_value='All'
        GROUP BY season ORDER BY season
    """, [player_id])
    by_pitch = query("""
        SELECT season, split_value as pitch_type, SUM(batters_faced) as batters_faced,
            CASE WHEN SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_velo IS NOT NULL THEN avg_velo*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END),1) END as avg_velo,
            CASE WHEN SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(CAST(SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN avg_spin_rate*batters_faced ELSE 0 END) AS REAL)
                    /SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)) END as avg_spin_rate
        FROM pitcher_splits
        WHERE player_id=? AND split_type='pitch_type'
        GROUP BY season, split_value ORDER BY season, split_value
    """, [player_id])
    return jsonify({'overall': overall, 'by_pitch': by_pitch})


# ---------------------------------------------------------------------------
# API — Pitch arsenal
# ---------------------------------------------------------------------------

_PT_NAMES = {
    'FF':'4-Seam FB','FA':'4-Seam FB','SI':'Sinker','FC':'Cutter','FO':'Forkball',
    'SL':'Slider','ST':'Sweeper','SV':'Slurve',
    'CU':'Curveball','KC':'Knuckle Curve','CS':'Slow Curve',
    'CH':'Changeup','FS':'Splitter',
    'EP':'Eephus','KN':'Knuckleball','SC':'Screwball','PO':'Pitchout',
}

@app.route("/api/pitcher/<int:player_id>/arsenal")
def api_pitcher_arsenal(player_id: int):
    season = request.args.get("season", SEASON, type=int)
    dates  = SEASON_DATES.get(season, SEASON_DATES[SEASON])

    splits = query("""
        SELECT split_value as pitch_type,
            SUM(batters_faced) as batters_faced,
            CASE WHEN SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN avg_velo IS NOT NULL THEN avg_velo*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN avg_velo IS NOT NULL THEN batters_faced ELSE 0 END),1) END as avg_velo,
            CASE WHEN SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN avg_spin_rate*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN avg_spin_rate IS NOT NULL THEN batters_faced ELSE 0 END)) END as avg_spin_rate,
            CASE WHEN SUM(batters_faced)>0
                THEN ROUND(CAST(SUM(k_pct*batters_faced) AS REAL)/SUM(batters_faced),3) END as k_pct,
            CASE WHEN SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN woba_against IS NOT NULL THEN woba_against*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN woba_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as woba_against,
            CASE WHEN SUM(CASE WHEN avg_against IS NOT NULL THEN batters_faced ELSE 0 END)>0
                THEN ROUND(SUM(CASE WHEN avg_against IS NOT NULL THEN avg_against*batters_faced ELSE 0 END)
                    /SUM(CASE WHEN avg_against IS NOT NULL THEN batters_faced ELSE 0 END),3) END as avg_against
        FROM pitcher_splits
        WHERE player_id=? AND season=? AND split_type='pitch_type'
        GROUP BY split_value
        ORDER BY SUM(batters_faced) DESC
    """, (player_id, season))

    total_bf = sum(r["batters_faced"] or 0 for r in splits)

    whiff_rows = query("""
        SELECT pitch_type,
            ROUND(CAST(SUM(CASE WHEN description IN
                ('swinging_strike','swinging_strike_blocked','missed_bunt')
                THEN 1 ELSE 0 END) AS REAL) /
            NULLIF(SUM(CASE WHEN description IN
                ('swinging_strike','swinging_strike_blocked',
                 'foul','foul_tip','hit_into_play','hit_into_play_no_out',
                 'hit_into_play_score','foul_bunt','missed_bunt')
                THEN 1 ELSE 0 END), 0) * 100, 1) as whiff_pct
        FROM pitches
        WHERE pitcher=? AND date(game_date) BETWEEN ? AND ?
          AND pitch_type IS NOT NULL
        GROUP BY pitch_type
    """, (player_id, dates["season_start"], dates["season_end"]))
    whiff_map = {r["pitch_type"]: r["whiff_pct"] for r in whiff_rows}

    result = []
    for r in splits:
        pt = r["pitch_type"]
        result.append({
            "pitch_type":    pt,
            "pitch_name":    _PT_NAMES.get(pt, pt),
            "usage_pct":     round(r["batters_faced"] / total_bf * 100, 1) if total_bf > 0 else 0,
            "avg_velo":      r["avg_velo"],
            "avg_spin_rate": r["avg_spin_rate"],
            "whiff_pct":     whiff_map.get(pt),
            "avg_against":   r["avg_against"],
            "woba_against":  r["woba_against"],
        })
    return jsonify(result)


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
# API — Recent form (rolling N-day window)
# ---------------------------------------------------------------------------

@app.route("/api/batter/<int:player_id>/recent")
def api_batter_recent(player_id: int):
    import pandas as pd
    from process import compute_batter_stats
    window = request.args.get("window", 30, type=int)
    season = request.args.get("season", SEASON, type=int)
    dates  = SEASON_DATES.get(season, SEASON_DATES[SEASON])

    max_date_row = query(
        "SELECT MAX(date(game_date)) as md FROM pitches WHERE batter=? "
        "AND date(game_date) BETWEEN ? AND ?",
        (player_id, dates["season_start"], dates["season_end"])
    )
    max_date = max_date_row[0]["md"] if max_date_row else None
    if not max_date:
        return jsonify({"stats": {}, "games": 0})

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM pitches WHERE batter=? "
        "AND date(game_date) BETWEEN date(?, ?) AND ?",
        conn, params=(player_id, max_date, f"-{window} days", max_date)
    )
    conn.close()
    if df.empty:
        return jsonify({"stats": {}, "games": 0})

    stats = compute_batter_stats(df)
    return jsonify({"stats": stats, "games": int(df["game_pk"].nunique()), "window": window})


@app.route("/api/pitcher/<int:player_id>/recent")
def api_pitcher_recent(player_id: int):
    import pandas as pd
    from process import compute_pitcher_stats
    window = request.args.get("window", 30, type=int)
    season = request.args.get("season", SEASON, type=int)
    dates  = SEASON_DATES.get(season, SEASON_DATES[SEASON])

    max_date_row = query(
        "SELECT MAX(date(game_date)) as md FROM pitches WHERE pitcher=? "
        "AND date(game_date) BETWEEN ? AND ?",
        (player_id, dates["season_start"], dates["season_end"])
    )
    max_date = max_date_row[0]["md"] if max_date_row else None
    if not max_date:
        return jsonify({"stats": {}, "games": 0})

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM pitches WHERE pitcher=? "
        "AND date(game_date) BETWEEN date(?, ?) AND ?",
        conn, params=(player_id, max_date, f"-{window} days", max_date)
    )
    conn.close()
    if df.empty:
        return jsonify({"stats": {}, "games": 0})

    stats = compute_pitcher_stats(df)
    return jsonify({"stats": stats, "games": int(df["game_pk"].nunique()), "window": window})


# ---------------------------------------------------------------------------
# API — Raw pitch data (heat maps, spray, movement)
# ---------------------------------------------------------------------------

@app.route("/api/batter/<int:player_id>/pitches")
def api_batter_pitches(player_id: int):
    season = request.args.get("season", SEASON, type=int)
    if season < PITCH_DATA_MIN_SEASON:
        return jsonify({"error": "no_pitch_data", "min_season": PITCH_DATA_MIN_SEASON})
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
    season = request.args.get("season", SEASON, type=int)
    if season < PITCH_DATA_MIN_SEASON:
        return jsonify({"error": "no_pitch_data", "min_season": PITCH_DATA_MIN_SEASON})
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
    if season < PITCH_DATA_MIN_SEASON:
        return jsonify({"error": "no_pitch_data", "min_season": PITCH_DATA_MIN_SEASON})
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
    if season < PITCH_DATA_MIN_SEASON:
        return jsonify({"error": "no_pitch_data", "min_season": PITCH_DATA_MIN_SEASON})
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


@app.route("/robots.txt")
def robots():
    return app.send_static_file("robots.txt")


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
        SELECT b.player_id, p.full_name,
               GROUP_CONCAT(DISTINCT p.team) as team, p.position,
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
        GROUP BY b.player_id
        ORDER BY b.woba DESC
        """,
        (season, min_pa)
    )

    pitchers = query(
        """
        SELECT b.player_id, p.full_name,
               GROUP_CONCAT(DISTINCT p.team) as team, p.position,
               b.era, b.whip, b.innings_pitched, b.batters_faced,
               b.strikeouts, b.walks_allowed, b.hits_allowed, b.hbp,
               b.home_runs_allowed,
               b.k_pct, b.bb_pct, b.k_bb, b.whiff_pct,
               b.avg_against, b.obp_against, b.slg_against, b.woba_against,
               b.avg_velo, b.avg_spin_rate,
               b.fip, b.era_plus
        FROM pitcher_splits b
        JOIN players p ON b.player_id = p.player_id AND b.season = p.season
        WHERE b.season = ? AND b.split_type = 'overall'
              AND b.innings_pitched >= ?
        GROUP BY b.player_id
        ORDER BY b.era ASC
        """,
        (season, min_ip)
    )

    available_seasons = [r["season"] for r in query(
        "SELECT DISTINCT season FROM batter_splits ORDER BY season DESC"
    )]

    # Classify pitchers as SP/RP using pre-computed pitcher_splits (inning=1, BF threshold)
    pitcher_ids = tuple(p["player_id"] for p in pitchers)
    if pitcher_ids:
        placeholders = ",".join("?" * len(pitcher_ids))
        starter_rows = query(
            f"SELECT player_id FROM pitcher_splits "
            f"WHERE player_id IN ({placeholders}) AND season=? "
            f"AND split_type='inning' AND split_value='1' AND batters_faced >= 25",
            pitcher_ids + (season,)
        )
        starter_ids = {r["player_id"] for r in starter_rows}
    else:
        starter_ids = set()

    return render_template(
        "leaderboards.html",
        batters=batters,
        pitchers=pitchers,
        season=season,
        available_seasons=available_seasons,
        min_pa=min_pa,
        min_ip=min_ip,
        starter_ids=starter_ids,
    )




# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------

@app.route("/sitemap.xml")
def sitemap():
    """Generate sitemap for all player pages + static pages."""
    base = "https://dugoutintel.com"
    today = __import__("datetime").date.today().isoformat()

    players = query(
        f"SELECT player_id, position_type FROM players WHERE season = {SEASON}"
    )
    available_teams = query("SELECT DISTINCT team FROM players")

    def url_entry(loc, lastmod=today, changefreq="weekly", priority="0.5"):
        return (
            f"  <url>"
            f"<loc>{loc}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority>"
            f"</url>"
        )

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        # Static pages
        url_entry(base + "/",             changefreq="daily",   priority="1.0"),
        url_entry(base + "/leaderboards", changefreq="daily",   priority="0.9"),
        url_entry(base + "/glossary",     changefreq="monthly", priority="0.6"),
        url_entry(base + "/about",        changefreq="monthly", priority="0.5"),
        url_entry(base + "/privacy",      changefreq="yearly",  priority="0.3"),
    ]

    for r in available_teams:
        code = r["team"].lower()
        xml_lines.append(url_entry(f"{base}/{code}",          changefreq="daily",  priority="0.8"))
        xml_lines.append(url_entry(f"{base}/{code}/stats",    changefreq="daily",  priority="0.7"))

    for p in players:
        route = "pitcher" if p["position_type"] == "Pitcher" else "batter"
        xml_lines.append(url_entry(f"{base}/{route}/{p['player_id']}", changefreq="daily", priority="0.7"))

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
