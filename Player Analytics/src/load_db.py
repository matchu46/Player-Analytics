"""
load_db.py — Create the SQLite database schema and load raw CSV data into it.

Usage:
    python src/load_db.py --create              # Create baseball.db schema
    python src/load_db.py --load   --team ARI   # Load CSVs for a team
    python src/load_db.py --all    --team ARI   # Create + load all
    python src/load_db.py --value  --team ARI   # Load WAR/salary/awards
    python src/load_db.py --defense --team ARI  # Load defensive metrics
    python src/load_db.py --migrate             # One-time: dbacks.db → baseball.db
"""

import argparse
import difflib
import os
import sqlite3
import unicodedata
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "db", "baseball.db"))
OLD_DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
-- -----------------------------------------------------------------------
-- Players / Roster
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    player_id       INTEGER,
    full_name       TEXT NOT NULL,
    jersey_number   TEXT,
    position        TEXT,          -- C, 1B, SS, SP, RP, etc.
    position_type   TEXT,          -- Pitcher or Hitter
    season          INTEGER NOT NULL,
    status          TEXT,
    team            TEXT NOT NULL DEFAULT 'ARI',
    PRIMARY KEY (player_id, team, season)
);

-- -----------------------------------------------------------------------
-- Statcast pitch-by-pitch data
-- Each row = one pitch thrown in a game involving any tracked team
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pitches (
    -- Identity
    game_pk             INTEGER,
    game_date           TEXT,
    at_bat_number       INTEGER,
    pitch_number        INTEGER,

    -- Participants
    batter              INTEGER,    -- MLBAM player ID
    pitcher             INTEGER,    -- MLBAM player ID
    stand               TEXT,       -- Batter handedness: L/R
    p_throws            TEXT,       -- Pitcher handedness: L/R

    -- Teams / venue
    home_team           TEXT,
    away_team           TEXT,
    batting_team        TEXT,       -- Derived: which team is batting

    -- Situation
    inning              INTEGER,
    inning_topbot       TEXT,       -- Top / Bot
    balls               INTEGER,
    strikes             INTEGER,
    outs_when_up        INTEGER,
    on_1b               REAL,       -- MLBAM ID of runner (NULL if empty)
    on_2b               REAL,
    on_3b               REAL,

    -- Pitch outcome
    type                TEXT,       -- S=strike, B=ball, X=in play
    description         TEXT,       -- "called_strike", "swinging_strike", etc.
    events              TEXT,       -- Terminal event: "home_run", "strikeout", etc.
    bb_type             TEXT,       -- ground_ball, fly_ball, line_drive, popup

    -- Pitch metrics
    pitch_type          TEXT,       -- FF, SL, CH, etc.
    release_speed       REAL,
    release_spin_rate   REAL,
    effective_speed     REAL,
    pfx_x               REAL,       -- Horizontal movement (inches)
    pfx_z               REAL,       -- Vertical movement (inches)
    plate_x             REAL,       -- Horizontal location at plate
    plate_z             REAL,       -- Vertical location at plate

    -- Batted ball metrics
    launch_speed        REAL,       -- Exit velocity (mph)
    launch_angle        REAL,
    hit_distance_sc     REAL,
    hc_x                REAL,       -- Hit coordinate X
    hc_y                REAL,       -- Hit coordinate Y
    estimated_ba_using_speedangle REAL,
    estimated_woba_using_speedangle REAL,

    -- Game context
    bat_score           INTEGER,    -- Batting team score
    fld_score           INTEGER,    -- Fielding team score
    post_bat_score      INTEGER,
    post_fld_score      INTEGER,
    data_side           TEXT,       -- 'pitching' or 'batting' (fetch context)

    PRIMARY KEY (game_pk, at_bat_number, pitch_number)
);

-- -----------------------------------------------------------------------
-- Pre-computed situational splits (derived from pitches table)
-- Rebuilt by process.py after any data load
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS batter_splits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL,
    season          INTEGER NOT NULL,
    split_type      TEXT NOT NULL,   -- 'inning', 'count', 'runners', 'venue', 'handedness', etc.
    split_value     TEXT NOT NULL,   -- e.g., '7', '2-2', 'RISP', 'Chase Field', 'vs_LHP'
    team            TEXT NOT NULL DEFAULT 'ARI',

    -- Counting stats
    pa              INTEGER DEFAULT 0,   -- plate appearances
    ab              INTEGER DEFAULT 0,
    hits            INTEGER DEFAULT 0,
    singles         INTEGER DEFAULT 0,
    doubles         INTEGER DEFAULT 0,
    triples         INTEGER DEFAULT 0,
    home_runs       INTEGER DEFAULT 0,
    rbi             INTEGER DEFAULT 0,
    walks           INTEGER DEFAULT 0,
    strikeouts      INTEGER DEFAULT 0,
    hbp             INTEGER DEFAULT 0,

    -- Rate stats (stored as TEXT to handle NULL cleanly; computed in Python)
    avg             REAL,
    obp             REAL,
    slg             REAL,
    ops             REAL,
    woba            REAL,

    -- Batted ball
    avg_exit_velo   REAL,
    avg_launch_angle REAL,
    hard_hit_pct    REAL,   -- % balls in play with EV >= 95 mph
    barrel_pct      REAL,

    -- Swing/contact
    swing_pct       REAL,
    whiff_pct       REAL,
    contact_pct     REAL,

    -- Advanced (computed post-process using league constants)
    babip           REAL,
    ops_plus        REAL,
    wrc_plus        REAL,

    UNIQUE (team, player_id, season, split_type, split_value)
);

CREATE TABLE IF NOT EXISTS pitcher_splits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL,
    season          INTEGER NOT NULL,
    split_type      TEXT NOT NULL,
    split_value     TEXT NOT NULL,
    team            TEXT NOT NULL DEFAULT 'ARI',

    -- Counting stats
    batters_faced   INTEGER DEFAULT 0,
    innings_pitched REAL,
    hits_allowed    INTEGER DEFAULT 0,
    runs_allowed    INTEGER DEFAULT 0,
    earned_runs     INTEGER DEFAULT 0,
    home_runs_allowed INTEGER DEFAULT 0,
    walks_allowed   INTEGER DEFAULT 0,
    strikeouts      INTEGER DEFAULT 0,
    hbp             INTEGER DEFAULT 0,

    -- Rate stats
    era             REAL,
    whip            REAL,
    k_pct           REAL,
    bb_pct          REAL,
    k_bb            REAL,
    avg_against     REAL,
    obp_against     REAL,
    slg_against     REAL,
    woba_against    REAL,

    -- Pitch mix (JSON stored as text: {"FF": 45.2, "SL": 30.1, ...})
    pitch_mix       TEXT,

    -- Pitch metrics
    avg_velo        REAL,
    avg_spin_rate   REAL,

    -- Advanced (computed post-process using league constants)
    fip             REAL,
    era_plus        REAL,

    UNIQUE (team, player_id, season, split_type, split_value)
);

-- -----------------------------------------------------------------------
-- Player value: WAR, salary, $/WAR (from FanGraphs via pybaseball)
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_value (
    player_id       INTEGER,
    season          INTEGER,
    team            TEXT NOT NULL DEFAULT 'ARI',
    war             REAL,
    salary          INTEGER,        -- annual value in dollars
    dollars_per_war REAL,
    PRIMARY KEY (player_id, team, season)
);

-- -----------------------------------------------------------------------
-- Player awards: All-Star, Gold Glove, Silver Slugger, MVP, etc.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_awards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER,
    season      INTEGER,
    team        TEXT NOT NULL DEFAULT 'ARI',
    award_name  TEXT,
    UNIQUE (team, player_id, season, award_name)
);

-- -----------------------------------------------------------------------
-- Player defense: fielding stats + sprint speed
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_defense (
    player_id    INTEGER,
    season       INTEGER,
    team         TEXT NOT NULL DEFAULT 'ARI',
    position     TEXT,
    games        INTEGER,
    innings      REAL,
    errors       INTEGER,
    fielding_pct REAL,
    drs          REAL,
    def_runs     REAL,
    oaa          REAL,
    sprint_speed REAL,
    sprint_pct   INTEGER,
    PRIMARY KEY (player_id, team, season)
);

-- -----------------------------------------------------------------------
-- Indexes for fast web queries
-- -----------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_pitches_batter   ON pitches(batter);
CREATE INDEX IF NOT EXISTS idx_pitches_pitcher  ON pitches(pitcher);
CREATE INDEX IF NOT EXISTS idx_pitches_game     ON pitches(game_pk);
CREATE INDEX IF NOT EXISTS idx_pitches_date     ON pitches(game_date);
CREATE INDEX IF NOT EXISTS idx_batter_splits    ON batter_splits(player_id, season, split_type);
CREATE INDEX IF NOT EXISTS idx_pitcher_splits   ON pitcher_splits(player_id, season, split_type);
CREATE INDEX IF NOT EXISTS idx_batter_splits_team  ON batter_splits(team, player_id, season);
CREATE INDEX IF NOT EXISTS idx_pitcher_splits_team ON pitcher_splits(team, player_id, season);
"""


# ---------------------------------------------------------------------------
# Create DB
# ---------------------------------------------------------------------------

def create_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database created: {DB_PATH}")


# ---------------------------------------------------------------------------
# Load roster CSV -> players table
# ---------------------------------------------------------------------------

def load_roster(season: int = 2025, team: str = 'ARI'):
    path = os.path.join(RAW_DIR, f"roster_{team}_{season}.csv")
    # Fall back to old filename for backwards compat
    if not os.path.exists(path) and team == 'ARI':
        path = os.path.join(RAW_DIR, f"roster_{season}.csv")
    if not os.path.exists(path):
        print(f"Roster file not found: {path}. Run fetch.py --type roster --team {team} first.")
        return

    df = pd.read_csv(path)
    df["season"] = season
    df["team"] = team

    conn = sqlite3.connect(DB_PATH)
    # Delete existing rows for this team+season before inserting
    conn.execute("DELETE FROM players WHERE team=? AND season=?", (team, season))
    conn.commit()
    df.to_sql("players", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    print(f"Loaded {len(df)} roster entries for {team} {season}.")


# ---------------------------------------------------------------------------
# Load Statcast CSV -> pitches table
# ---------------------------------------------------------------------------

# Columns from Baseball Savant that we want to keep (subset for DB size)
PITCH_COLS = [
    "game_pk", "game_date", "at_bat_number", "pitch_number",
    "batter", "pitcher", "stand", "p_throws",
    "home_team", "away_team",
    "inning", "inning_topbot", "balls", "strikes", "outs_when_up",
    "on_1b", "on_2b", "on_3b",
    "type", "description", "events", "bb_type",
    "pitch_type", "release_speed", "release_spin_rate", "effective_speed",
    "pfx_x", "pfx_z", "plate_x", "plate_z",
    "launch_speed", "launch_angle", "hit_distance_sc", "hc_x", "hc_y",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "bat_score", "fld_score", "post_bat_score", "post_fld_score",
    "data_side",
]


def load_statcast(season: int = 2025, team: str = 'ARI'):
    path = os.path.join(RAW_DIR, f"statcast_{team}_{season}.csv")
    # Fall back to old filename for backwards compat
    if not os.path.exists(path) and team == 'ARI':
        path = os.path.join(RAW_DIR, f"statcast_{season}.csv")
    if not os.path.exists(path):
        print(f"Statcast file not found: {path}. Run fetch.py --type statcast --team {team} first.")
        return

    print(f"Loading {path}...")
    df = pd.read_csv(path, low_memory=False)

    # Derive batting_team column
    df["batting_team"] = df.apply(
        lambda r: r["home_team"] if r["inning_topbot"] == "Bot" else r["away_team"],
        axis=1,
    )

    # Keep only columns we care about (ignore missing ones gracefully)
    keep = [c for c in PITCH_COLS if c in df.columns]
    df = df[keep]

    # Deduplicate within CSV (NaN-safe: drop rows missing key columns first)
    df = df.dropna(subset=["game_pk", "at_bat_number", "pitch_number"])
    df = df.drop_duplicates(subset=["game_pk", "at_bat_number", "pitch_number"])

    conn = sqlite3.connect(DB_PATH)

    # For pitches, we use INSERT OR IGNORE since multiple teams may share pitches
    # (a D-backs pitcher appears in both ARI and LAD data)
    chunk_size = 50_000
    total = 0
    for start in range(0, len(df), chunk_size):
        chunk = df.iloc[start:start + chunk_size]
        cols = list(chunk.columns)
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT OR IGNORE INTO pitches ({','.join(cols)}) VALUES ({placeholders})"
        conn.executemany(sql, chunk.itertuples(index=False, name=None))
        total += len(chunk)
        conn.commit()
        print(f"  Inserted {total:,}/{len(df):,} pitches...")
    conn.close()
    print(f"Done. {total:,} pitches loaded for {team} {season}.")


# ---------------------------------------------------------------------------
# Load value + awards CSVs -> player_value, player_awards tables
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Lowercase, strip accents, remove suffixes for fuzzy matching."""
    name = str(name).strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    for suffix in [" jr.", " sr.", " ii", " iii", " iv"]:
        name = name.replace(suffix, "")
    return name.strip()


def load_value(season: int = 2025, team: str = 'ARI'):
    value_path = os.path.join(RAW_DIR, f"value_{team}_{season}.csv")
    awards_path = os.path.join(RAW_DIR, f"awards_{team}_{season}.csv")
    # Fall back to old filenames for backwards compat
    if not os.path.exists(value_path) and team == 'ARI':
        value_path = os.path.join(RAW_DIR, f"value_{season}.csv")
    if not os.path.exists(awards_path) and team == 'ARI':
        awards_path = os.path.join(RAW_DIR, f"awards_{season}.csv")

    conn = sqlite3.connect(DB_PATH)

    # Build name -> player_id lookup from players table
    rows = conn.execute(
        "SELECT player_id, full_name FROM players WHERE season=? AND team=?", (season, team)
    ).fetchall()
    name_to_id = {_normalize_name(r[1]): r[0] for r in rows}
    norm_names = list(name_to_id.keys())

    def resolve_id(fg_name):
        norm = _normalize_name(fg_name)
        if norm in name_to_id:
            return name_to_id[norm]
        matches = difflib.get_close_matches(norm, norm_names, n=1, cutoff=0.82)
        if matches:
            print(f"  Fuzzy match: '{fg_name}' -> '{matches[0]}'")
            return name_to_id[matches[0]]
        print(f"  WARNING: No match for '{fg_name}'")
        return None

    # Load WAR + salary
    if os.path.exists(value_path):
        df = pd.read_csv(value_path)
        loaded = 0
        for _, row in df.iterrows():
            pid = resolve_id(row["Name"])
            if pid is None:
                continue
            war = float(row["WAR"]) if pd.notna(row.get("WAR")) else None
            salary = int(row["Salary"]) if pd.notna(row.get("Salary")) else None
            dol_per_war = None
            if war and war > 0 and salary:
                dol_per_war = round(salary / war / 1_000_000, 2)
            conn.execute(
                "INSERT OR REPLACE INTO player_value (player_id, season, team, war, salary, dollars_per_war) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, season, team, war, salary, dol_per_war),
            )
            loaded += 1
        conn.commit()
        print(f"Loaded {loaded} player value rows for {team} {season}.")
    else:
        print(f"Value file not found: {value_path}. Run fetch_value.py --team {team} first.")

    # Load awards
    if os.path.exists(awards_path):
        df = pd.read_csv(awards_path)
        loaded = 0
        for _, row in df.iterrows():
            conn.execute(
                "INSERT OR IGNORE INTO player_awards (player_id, season, team, award_name) VALUES (?, ?, ?, ?)",
                (int(row["player_id"]), int(row["season"]), team, str(row["award_name"])),
            )
            loaded += 1
        conn.commit()
        print(f"Loaded {loaded} award rows for {team} {season}.")
    else:
        print(f"Awards file not found: {awards_path}. Run fetch_value.py --team {team} first.")

    conn.close()


def load_defense(season: int = 2025, team: str = 'ARI'):
    defense_path = os.path.join(RAW_DIR, f"defense_{team}_{season}.csv")
    # Fall back to old filename for backwards compat
    if not os.path.exists(defense_path) and team == 'ARI':
        defense_path = os.path.join(RAW_DIR, f"defense_{season}.csv")
    if not os.path.exists(defense_path):
        print(f"Defense file not found: {defense_path}. Run fetch_defense.py --team {team} first.")
        return

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_csv(defense_path)
    loaded = 0
    for _, row in df.iterrows():
        pid = row.get("player_id")
        if pd.isna(pid):
            continue
        pid = int(pid)

        def _f(col):
            v = row.get(col)
            return float(v) if pd.notna(v) else None

        def _i(col):
            v = row.get(col)
            return int(v) if pd.notna(v) else None

        conn.execute(
            "INSERT OR REPLACE INTO player_defense "
            "(player_id, season, team, position, games, innings, errors, fielding_pct, "
            " drs, def_runs, oaa, sprint_speed, sprint_pct) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid, season, team,
                str(row.get("position")) if pd.notna(row.get("position")) else None,
                _i("games"), _f("innings"), _i("errors"), _f("fielding_pct"),
                _f("drs"), _f("def_runs"), _f("oaa"),
                _f("sprint_speed"), _i("sprint_pct"),
            ),
        )
        loaded += 1
    conn.commit()
    conn.close()
    print(f"Loaded {loaded} player defense rows for {team} {season}.")


# ---------------------------------------------------------------------------
# One-time migration: dbacks.db -> baseball.db with team='ARI'
# ---------------------------------------------------------------------------

def migrate():
    """Copy all data from dbacks.db into baseball.db, adding team='ARI' everywhere."""
    if not os.path.exists(OLD_DB_PATH):
        print(f"Old database not found at {OLD_DB_PATH}. Nothing to migrate.")
        return

    print(f"Migrating {OLD_DB_PATH} -> {DB_PATH}")

    # Create new DB with current schema
    create_db()

    old = sqlite3.connect(OLD_DB_PATH)
    new = sqlite3.connect(DB_PATH)
    old.row_factory = sqlite3.Row

    # --- players ---
    rows = old.execute("SELECT * FROM players").fetchall()
    print(f"  Migrating {len(rows)} players...")
    for r in rows:
        d = dict(r)
        new.execute(
            "INSERT OR IGNORE INTO players "
            "(player_id, full_name, jersey_number, position, position_type, season, status, team) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (d['player_id'], d['full_name'], d.get('jersey_number'), d.get('position'),
             d.get('position_type'), d['season'], d.get('status'), 'ARI')
        )

    # --- pitches (no team column needed — already has home_team/away_team) ---
    rows = old.execute("SELECT * FROM pitches").fetchall()
    print(f"  Migrating {len(rows):,} pitches...")
    chunk_size = 50_000
    total = 0
    all_rows = list(rows)
    for start in range(0, len(all_rows), chunk_size):
        chunk = all_rows[start:start + chunk_size]
        for r in chunk:
            d = dict(r)
            cols = list(d.keys())
            vals = [d[c] for c in cols]
            ph = ','.join(['?'] * len(cols))
            new.execute(
                f"INSERT OR IGNORE INTO pitches ({','.join(cols)}) VALUES ({ph})", vals
            )
        total += len(chunk)
        new.commit()
        print(f"    {total:,}/{len(all_rows):,} pitches...")

    # --- batter_splits ---
    rows = old.execute("SELECT * FROM batter_splits").fetchall()
    print(f"  Migrating {len(rows)} batter_splits...")
    for r in rows:
        d = dict(r)
        d.pop('id', None)
        d['team'] = 'ARI'
        cols = list(d.keys())
        ph = ','.join(['?'] * len(cols))
        new.execute(
            f"INSERT OR IGNORE INTO batter_splits ({','.join(cols)}) VALUES ({ph})",
            [d[c] for c in cols]
        )

    # --- pitcher_splits ---
    rows = old.execute("SELECT * FROM pitcher_splits").fetchall()
    print(f"  Migrating {len(rows)} pitcher_splits...")
    for r in rows:
        d = dict(r)
        d.pop('id', None)
        d['team'] = 'ARI'
        cols = list(d.keys())
        ph = ','.join(['?'] * len(cols))
        new.execute(
            f"INSERT OR IGNORE INTO pitcher_splits ({','.join(cols)}) VALUES ({ph})",
            [d[c] for c in cols]
        )

    # --- player_value ---
    rows = old.execute("SELECT * FROM player_value").fetchall()
    print(f"  Migrating {len(rows)} player_value rows...")
    for r in rows:
        d = dict(r)
        new.execute(
            "INSERT OR IGNORE INTO player_value (player_id, season, team, war, salary, dollars_per_war) "
            "VALUES (?,?,?,?,?,?)",
            (d['player_id'], d['season'], 'ARI', d.get('war'), d.get('salary'), d.get('dollars_per_war'))
        )

    # --- player_awards ---
    rows = old.execute("SELECT * FROM player_awards").fetchall()
    print(f"  Migrating {len(rows)} player_awards rows...")
    for r in rows:
        d = dict(r)
        new.execute(
            "INSERT OR IGNORE INTO player_awards (player_id, season, team, award_name) VALUES (?,?,?,?)",
            (d['player_id'], d['season'], 'ARI', d.get('award_name'))
        )

    # --- player_defense ---
    try:
        rows = old.execute("SELECT * FROM player_defense").fetchall()
        print(f"  Migrating {len(rows)} player_defense rows...")
        for r in rows:
            d = dict(r)
            new.execute(
                "INSERT OR IGNORE INTO player_defense "
                "(player_id, season, team, position, games, innings, errors, fielding_pct, "
                " drs, def_runs, oaa, sprint_speed, sprint_pct) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d['player_id'], d['season'], 'ARI',
                 d.get('position'), d.get('games'), d.get('innings'), d.get('errors'),
                 d.get('fielding_pct'), d.get('drs'), d.get('def_runs'), d.get('oaa'),
                 d.get('sprint_speed'), d.get('sprint_pct'))
            )
    except sqlite3.OperationalError:
        print("  (No player_defense table in old DB — skipping)")

    new.commit()
    old.close()
    new.close()
    print(f"Migration complete. New DB: {DB_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/load baseball SQLite database")
    parser.add_argument("--create",  action="store_true", help="Create schema")
    parser.add_argument("--load",    action="store_true", help="Load roster + statcast CSVs")
    parser.add_argument("--value",   action="store_true", help="Load WAR/salary/awards CSVs")
    parser.add_argument("--defense", action="store_true", help="Load defensive metrics CSV")
    parser.add_argument("--all",     action="store_true", help="Create + load everything")
    parser.add_argument("--migrate", action="store_true", help="One-time: migrate dbacks.db → baseball.db")
    parser.add_argument("--team",    type=str, default="ARI", help="Team code (e.g. ARI, LAD)")
    parser.add_argument("--season",  type=int, default=2025)
    args = parser.parse_args()

    team = args.team.upper()

    if args.migrate:
        migrate()
    else:
        if args.create or args.all:
            create_db()

        if args.load or args.all:
            load_roster(args.season, team)
            load_statcast(args.season, team)

        if args.value or args.all:
            load_value(args.season, team)

        if args.defense or args.all:
            load_defense(args.season, team)
