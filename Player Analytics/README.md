# Dugout Intel

An MLB player analytics web app live at **[dugoutintel.com](https://dugoutintel.com)**. Currently covering the **Arizona Diamondbacks 2025 season**, with plans to expand to all 30 teams. Built with Python, Flask, SQLite, and Plotly.

---

## Features

### Roster Page
- Browse all 2025 D-backs players organized by position (C, 1B, 2B, SS, LF, CF, RF, DH for batters; SP and RP for pitchers)
- Toggle between All Players, Position Players, and Pitchers tabs — sorted by jersey number
- Click any player card to open their full profile

### Payroll Page (`/payroll`)
- Full team payroll sorted by salary (sourced from Spotrac)
- Salary vs. fWAR scatter chart — click any dot to go to that player's profile
- $/WAR color-coded: green (<$5M), yellow ($5–10M), red (>$10M); 0 WAR shown as ∞, negative WAR shown explicitly
- Sortable table by salary, WAR, $/WAR, or position

### Player Profile Pages
Both batter and pitcher pages share a common layout with a persistent filter bar and five tabs:

**Persistent Filter Bar** — Restrict all charts and splits to a specific situation:
- Pitcher Hand (vs LHP / vs RHP) — batters only
- Batter Hand (vs LHB / vs RHB) — pitchers only
- Inning, Count (0-0 through 3-2), Runners on Base, Outs, Home/Away, Pitch Type

**Splits Tab** — Up to 2 side-by-side comparison panels:
- Split dimensions: Count, Inning, Runners, Handedness, Outs, Venue (Home/Away), Stadium, Pitch Type, Leverage, Score State
- Stats: AVG, OBP, SLG, OPS, wOBA, HR, K, BB, PA, EV, Hard Hit%, Barrel%, Whiff%, Swing% (batters); K%, BB%, K-BB, wOBA, AVG, Velo, BF, etc. (pitchers)
- Bar chart + detailed data table per panel

**Strike Zone Tab** — Pitch location visualization:
- Density Heat Map or Individual Pitches (colored by outcome)
- Filter by pitch type, outcome, and handedness split
- Side-by-side subplots with strike zone overlay

**Spray Chart / Pitch Movement Tab**:
- Batters: spray chart on a field diagram, color by outcome / hit type / EV / launch angle
- Pitchers: horizontal vs vertical break scatter, color by pitch type or velocity

**Batted Ball / Velocity Tab**:
- Batters: EV vs Launch Angle scatter + Exit Velocity histogram
- Pitchers: pitch usage % bar chart + velocity range by pitch type

**Value Tab** — Contract value analysis:
- 2025 salary (Spotrac), full-season fWAR (FanGraphs — includes combined stats for players who split time between teams), Cost per WAR
- $/WAR color-coded; 0 WAR shown as ∞, negative WAR shown explicitly
- 2025 season awards & honors (All-Star, Silver Slugger, Gold Glove, Player of Week/Month, etc.)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Data Fetch | `pybaseball` (FanGraphs/Statcast), `statsapi` (MLB Stats API), `requests`+`BeautifulSoup` (Spotrac) |
| Storage | SQLite (`data/db/dbacks.db`) |
| Backend | Python 3.12, Flask, Gunicorn |
| Charts | Plotly.js 2.35 |
| Frontend | Vanilla JS, CSS custom properties |
| Hosting | Railway.app (dugoutintel.com) |

---

## Project Structure

```
Player Analytics/
├── app/
│   ├── app.py                  # Flask routes + API endpoints
│   ├── static/css/style.css    # All styles
│   └── templates/
│       ├── base.html           # Site shell (header, footer, AdSense)
│       ├── index.html          # Roster page
│       ├── player.html         # Batter + pitcher profile page
│       ├── payroll.html        # Team payroll overview
│       ├── privacy.html        # Privacy policy
│       └── 404.html
├── src/
│   ├── fetch.py                # Pull roster + Statcast data from APIs
│   ├── fetch_value.py          # Pull WAR/salary/awards (FanGraphs + Spotrac + MLB API)
│   ├── load_db.py              # Create SQLite schema + load CSV data
│   └── process.py              # Compute all situational splits
├── data/
│   ├── db/dbacks.db            # SQLite database
│   └── raw/
│       ├── roster_2025.csv
│       ├── statcast_2025.csv
│       ├── value_2025.csv      # Full-season WAR + salary merged
│       └── awards_2025.csv
├── Procfile                    # Gunicorn start command for Railway
├── requirements.txt            # Full dev dependencies
└── requirements-prod.txt       # Production only (flask, flask-caching, gunicorn)
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
python src/fetch.py --type roster
python src/fetch.py --type statcast
python src/load_db.py --all
python src/process.py --all

# Value / salary / awards:
python src/fetch_value.py       # Fetch WAR (full-season), salary, awards
python src/load_db.py --value   # Load into DB
```

> **Note:** Fetching the full Statcast dataset takes 10–20 minutes due to rate limiting.

### 3. Start the web server
```bash
python app/app.py
```

Then open [http://localhost:5000](http://localhost:5000).

---

## Data Pipeline

```
fetch.py       →  roster_2025.csv, statcast_2025.csv
fetch_value.py →  value_2025.csv (full-season WAR + Spotrac salary), awards_2025.csv
    ↓
load_db.py     →  dbacks.db (players, pitches, player_value, player_awards)
    ↓
process.py     →  dbacks.db (batter_splits, pitcher_splits)
```

**Split dimensions:** overall, inning, count, runners, outs, handedness, venue (home/away), stadium, pitch type, score state, leverage

---

## Data Notes

- **Team code**: Statcast stores Arizona as `AZ` (not `ARI`). FanGraphs uses `ARI`.
- **Pitcher classification**: SP vs RP determined dynamically — pitcher who started in inning 1 in 3+ games = SP
- **WAR**: Full-season fWAR from FanGraphs. Players who split time between teams show combined season totals.
- **Salary**: Scraped from Spotrac using `requests` + `BeautifulSoup` (browser user-agent required)
- **$/WAR**: Calculated only for WAR > 0. 0 WAR displayed as ∞, negative WAR shown as the raw value.
- **wOBA weights**: walk: 0.696, HBP: 0.726, 1B: 0.883, 2B: 1.244, 3B: 1.569, HR: 2.007

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/batter/<id>/<split_type>` | Pre-computed batter splits |
| `GET /api/pitcher/<id>/<split_type>` | Pre-computed pitcher splits |
| `GET /api/batter/<id>/splits_live?...` | Live splits with active filters |
| `GET /api/pitcher/<id>/splits_live?...` | Live splits with active filters |
| `GET /api/batter/<id>/pitches?...` | Raw pitch coordinates + outcomes |
| `GET /api/pitcher/<id>/pitches?...` | Raw pitch coordinates + outcomes |
| `GET /api/pitcher/<id>/movement` | Pitch movement (pfx_x, pfx_z) |
| `GET /api/pitcher/<id>/velocity` | Velocity + usage by pitch type |
| `GET /api/batter/<id>/value` | WAR, salary, $/WAR, awards |
| `GET /api/pitcher/<id>/value` | WAR, salary, $/WAR, awards |
| `GET /sitemap.xml` | Auto-generated sitemap |

**Filter params:** `inning`, `balls`, `strikes`, `p_throws`, `stand`, `runners`, `home_away`, `outs`, `pitch_type`

---

*Data sourced from Baseball Savant, FanGraphs, Spotrac, and MLB Stats API. Not affiliated with MLB or any MLB team.*
