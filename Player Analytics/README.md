# D-backs Analytics — 2025

A local web application for exploring pitch-by-pitch Statcast data for the **Arizona Diamondbacks' 2025 season**. Built with Python, Flask, SQLite, and Plotly.

---

## Features

### Roster Page
- Browse all 2025 D-backs players organized by position (C, 1B, 2B, SS, LF, CF, RF, DH for batters; SP and RP for pitchers)
- Search by name or position
- Toggle between Position Players, Pitchers, and All Players tabs

### Player Profile Pages
Both batter and pitcher pages share a common layout:

**Persistent Filter Bar** — Restrict all charts and splits to a specific situation:
- Pitcher Hand (vs LHP / vs RHP) — batters only
- Batter Hand (vs LHB / vs RHB) — pitchers only
- Inning (1–9+), Count (0-0 through 3-2), Runners on Base, Outs
- Home / Away
- Pitch Type

**Splits Tab** — Up to 3 side-by-side comparison panels, each showing a selectable stat by a selectable split dimension:
- Split dimensions: Count, Inning, Runners, Handedness, Outs, Venue (Home/Away), Stadium, Pitch Type, Leverage, Score State
- Stats: AVG, OBP, SLG, OPS, wOBA, HR, K, BB, PA, EV, Hard Hit%, Barrel%, Whiff%, Swing% (batters); K%, BB%, K-BB, wOBA, AVG, Velo, BF, etc. (pitchers)
- Bar chart + detailed data table per panel
- Filters apply live when active

**Strike Zone Tab** — Pitch location visualization:
- Modes: Density Heat Map or Individual Pitches (colored by outcome)
- Filter by pitch type, outcome category, and pitcher/batter hand split
- Split view: side-by-side subplots with strike zone and home plate drawn in each panel

**Spray Chart Tab** (batters) — Batted ball locations on a Chase Field diagram:
- Color by outcome, hit type, batted ball type, exit velocity, or launch angle
- Hover shows pitch type, pitcher hand, pitch velocity, EV, and launch angle
- Supports pan and scroll-zoom

**Pitch Movement Tab** (pitchers) — Horizontal vs vertical break scatter:
- Color by pitch type or velocity

**Advanced Tab** — Batter: EV vs. Launch Angle scatter + Exit Velocity histogram. Pitcher: pitch usage % bar chart + velocity range by pitch type.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Data Fetch | `pybaseball` (Statcast), `statsapi` (MLB Stats API) |
| Storage | SQLite (`data/db/dbacks.db`) |
| Backend | Python 3.12, Flask |
| Charts | Plotly.js 2.35 |
| Frontend | Vanilla JS, CSS custom properties |

---

## Project Structure

```
Player Analytics/
├── app/
│   ├── app.py                  # Flask routes + API endpoints
│   ├── static/css/style.css    # All styles
│   └── templates/
│       ├── base.html           # Site shell (header, footer)
│       ├── index.html          # Roster page
│       ├── player.html         # Batter + pitcher profile page
│       └── 404.html
├── src/
│   ├── fetch.py                # Pull roster + Statcast data from APIs
│   ├── load_db.py              # Create SQLite schema + load CSV data
│   └── process.py              # Compute all situational splits
├── data/
│   ├── db/dbacks.db            # SQLite database
│   └── raw/
│       ├── roster_2025.csv
│       └── statcast_2025.csv
├── run_pipeline.py             # One-command pipeline runner
└── requirements.txt
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the full data pipeline
```bash
# Fetch roster + Statcast data, load DB, compute all splits
python run_pipeline.py

# Or run individual steps:
python src/fetch.py --type roster      # Roster only
python src/fetch.py --type statcast    # Statcast pitch data only
python src/load_db.py --all            # Create schema + load CSVs
python src/process.py --all            # Recompute all splits
```

> **Note:** Fetching the full Statcast dataset takes 10–20 minutes due to rate limiting. Cached results are reused on subsequent runs.

### 3. Start the web server
```bash
python app/app.py
```

Then open [http://localhost:5000](http://localhost:5000).

---

## Data Pipeline Details

```
fetch.py  →  data/raw/roster_2025.csv
             data/raw/statcast_2025.csv
    ↓
load_db.py →  data/db/dbacks.db
               tables: players, pitches
    ↓
process.py →  data/db/dbacks.db
               tables: batter_splits, pitcher_splits
```

**Pitch data coverage (2025 season):**
- ~50,000 pitches across ~283 games
- Includes both pitching side (all pitches thrown by ARI) and batting side (all pitches faced by ARI batters)

**Split dimensions computed:** overall, inning, count, runners, outs, handedness, venue (home/away), stadium, pitch type, score state, leverage

---

## Data Notes

- **Team code**: Statcast stores Arizona as `AZ` (not `ARI`)
- **Pitcher classification**: SP vs RP is determined dynamically — any pitcher who started in inning 1 in 3+ games is classified as a starter
- **wOBA weights**: 2025 approximate linear weights (walk: 0.696, HBP: 0.726, 1B: 0.883, 2B: 1.244, 3B: 1.569, HR: 2.007)
- **Barrel definition**: EV ≥ 98 mph with launch angle between 26° and 30° (simplified)

---

## Pitch Type Reference

| Code | Name |
|---|---|
| FF | Four-Seam Fastball |
| SI | Sinker |
| FC | Cutter |
| SL | Slider |
| SW | Sweeper |
| ST | Sweeping Curve |
| CU | Curveball |
| KC | Knuckle Curve |
| CH | Changeup |
| FS | Splitter |
| FO | Forkball |
| EP | Eephus |
| KN | Knuckleball |

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/players` | All players (id, name, position, type) |
| `GET /api/batter/<id>/<split_type>` | Pre-computed batter splits |
| `GET /api/pitcher/<id>/<split_type>` | Pre-computed pitcher splits |
| `GET /api/batter/<id>/splits_live?...` | Live splits with active filters |
| `GET /api/pitcher/<id>/splits_live?...` | Live splits with active filters |
| `GET /api/batter/<id>/pitches?...` | Raw pitch coordinates + outcomes |
| `GET /api/pitcher/<id>/pitches?...` | Raw pitch coordinates + outcomes |
| `GET /api/pitcher/<id>/movement` | Pitch movement data (pfx_x, pfx_z) |
| `GET /api/pitcher/<id>/velocity` | Velocity + usage by pitch type |

**Filter query params** (all endpoints that accept `?...`):
`inning`, `balls`, `strikes`, `p_throws`, `stand`, `runners`, `home_away`, `outs`, `pitch_type`

---

*Data sourced from Baseball Savant / MLB Stats API. Not affiliated with the Arizona Diamondbacks or MLB.*
