"""
load_db.py — Create the SQLite database schema and load raw CSV data into it.

Usage:
    python src/load_db.py --create    # Create schema only
    python src/load_db.py --load      # Load CSVs into existing DB
    python src/load_db.py --all       # Create schema + load
"""

import argparse
import os
import sqlite3
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
DB_PATH = os.path.join(DATA_DIR, "db", "dbacks.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
-- -----------------------------------------------------------------------
-- Players / Roster
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    player_id       INTEGER PRIMARY KEY,
    full_name       TEXT NOT NULL,
    jersey_number   TEXT,
    position        TEXT,          -- C, 1B, SS, SP, RP, etc.
    position_type   TEXT,          -- Pitcher or Hitter
    season          INTEGER NOT NULL,
    status          TEXT
);

-- -----------------------------------------------------------------------
-- Statcast pitch-by-pitch data
-- Each row = one pitch thrown in a game involving ARI
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

    UNIQUE (player_id, season, split_type, split_value)
);

CREATE TABLE IF NOT EXISTS pitcher_splits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id       INTEGER NOT NULL,
    season          INTEGER NOT NULL,
    split_type      TEXT NOT NULL,
    split_value     TEXT NOT NULL,

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

    UNIQUE (player_id, season, split_type, split_value)
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

def load_roster(season: int = 2025):
    path = os.path.join(RAW_DIR, f"roster_{season}.csv")
    if not os.path.exists(path):
        print(f"Roster file not found: {path}. Run fetch.py --type roster first.")
        return

    df = pd.read_csv(path)
    df["season"] = season

    conn = sqlite3.connect(DB_PATH)
    # Upsert: replace existing rows for this season
    df.to_sql("players", conn, if_exists="replace" if season == 2025 else "append",
              index=False)
    conn.commit()
    conn.close()
    print(f"Loaded {len(df)} roster entries for {season}.")


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


def load_statcast(season: int = 2025):
    path = os.path.join(RAW_DIR, f"statcast_{season}.csv")
    if not os.path.exists(path):
        print(f"Statcast file not found: {path}. Run fetch.py --type statcast first.")
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

    # Clear existing data so re-runs don't hit UNIQUE constraint errors
    conn.execute("DELETE FROM pitches")
    conn.commit()

    # Load row by row via executemany using INSERT OR IGNORE as a safety net
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
    print(f"Done. {total:,} pitches loaded.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/load D-backs SQLite database")
    parser.add_argument("--create", action="store_true", help="Create schema")
    parser.add_argument("--load", action="store_true", help="Load CSV data")
    parser.add_argument("--all", action="store_true", help="Create + load")
    parser.add_argument("--season", type=int, default=2025)
    args = parser.parse_args()

    if args.create or args.all:
        create_db()

    if args.load or args.all:
        load_roster(args.season)
        load_statcast(args.season)
