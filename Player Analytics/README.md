# Dugout Intel

An MLB player analytics web app live at **[dugoutintel.com](https://dugoutintel.com)**. Currently covering **all NL West teams** (Arizona Diamondbacks, Los Angeles Dodgers, San Francisco Giants, San Diego Padres, Colorado Rockies) for the 2025 season — built for easy expansion to all 30 teams. Built with Python, Flask, SQLite, and Plotly.

---

## Features

### Team Picker (`/`)
- Homepage shows all teams with loaded data
- "Coming Soon" grid for teams not yet loaded
- Team-specific color theming throughout the site

### Roster Page (`/<team>`, e.g. `/ari`, `/lad`)
- Browse all players organized by position (C, 1B, 2B, SS, LF, CF, RF, DH for batters; SP and RP for pitchers)
- Toggle between All Players, Position Players, and Pitchers tabs — sorted by jersey number
- Click any player card to open their full profile

### Payroll Page (`/<team>/payroll`, e.g. `/ari/payroll`)
- Full team payroll sorted by salary (sourced from Spotrac)
- Salary vs. fWAR scatter chart — click any dot to go to that player's profile
- $/WAR color-coded: green (<$5M), yellow ($5–10M), red (>$10M); 0 WAR shown as ∞, negative WAR shown explicitly
- Sortable table by salary, WAR, $/WAR, or position

### Player Profile Pages (`/batter/<id>`, `/pitcher/<id>`)
Both batter and pitcher pages share a common layout with a persistent filter bar and five tabs:

**Persistent Filter Bar** — Restrict all charts and splits to a specific situation:
- Pitcher Hand (vs LHP / vs RHP) — batters only
- Batter Hand (vs LHB / vs RHB) — pitchers only
- Inning, Count (0-0 through 3-2), Runners on Base, Outs, Home/Away, Pitch Type
- **vs. Player search** — type a pitcher/batter name to filter stats to matchups against that specific player

**Splits Tab** — Up to 2 side-by-side comparison panels:
- Split dimensions: Count, Inning, Runners, Handedness, Outs, Venue (Home/Away), Stadium, Pitch Type, Leverage, Score State, Month, **Opponent Team, Opponent Division, Opponent League**
- Batter stats: AVG, OBP, SLG, OPS, wOBA, HR, K, BB, PA, EV, Hard Hit%, Barrel%, Whiff%, Swing%
- Pitcher stats: **ERA, WHIP, IP**, K%, BB%, K-BB, wOBA, AVG against, Velo, BF, etc.

**Strike Zone Tab** — Pitch location visualization

**Spray Chart / Pitch Movement Tab**

**Batted Ball / Velocity Tab**

**Defense Tab** — Fielding stats (DRS, Def, OAA, Fld%) + sprint speed percentile bar

**Value Tab** — Salary, fWAR, $/WAR, season awards

---

## Tech Stack

| Layer | Technology |
|---|---|
| Data Fetch | `pybaseball` (FanGraphs/Statcast), `statsapi` (MLB Stats API), `requests`+`BeautifulSoup` (Spotrac) |
| Storage | SQLite (`data/db/baseball.db`) |
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
│       ├── teams.html          # Team picker homepage
│       ├── index.html          # Team roster page
│       ├── player.html         # Batter + pitcher profile page
│       ├── payroll.html        # Team payroll overview
│       ├── privacy.html        # Privacy policy
│       └── 404.html
├── src/
│   ├── teams.py                # Central config: all 30 MLB teams (division, league, colors, IDs)
│   ├── fetch.py                # Pull roster + Statcast data (--team ARI)
│   ├── fetch_value.py          # Pull WAR/salary/awards (--team ARI)
│   ├── fetch_defense.py        # Pull fielding stats + sprint speed (--team ARI)
│   ├── load_db.py              # Create SQLite schema + load CSV data (--team ARI)
│   └── process.py              # Compute all situational splits (--team ARI)
├── data/
│   ├── db/baseball.db          # SQLite database (single DB for all teams)
│   └── raw/
│       ├── roster_ARI_2025.csv
│       ├── statcast_ARI_2025.csv
│       ├── value_ARI_2025.csv
│       ├── awards_ARI_2025.csv
│       └── defense_ARI_2025.csv
├── Procfile                    # Gunicorn start command for Railway
├── requirements.txt            # Full dev dependencies
└── requirements-prod.txt       # Production only (flask, gunicorn)
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Migrate existing data (first time only)
If you have the old `dbacks.db`, migrate it to the new multi-team schema:
```bash
python src/load_db.py --migrate
```

### 3. Run the full data pipeline for a team
```bash
# Fetch roster + Statcast data
python src/fetch.py --team ARI --type all

# Load roster into DB first (required before fetch_value/fetch_defense for name matching)
python src/load_db.py --team ARI --all

# Fetch and load value (WAR/salary/awards) + defense
python src/fetch_value.py --team ARI
python src/load_db.py --team ARI --value

python src/fetch_defense.py --team ARI
python src/load_db.py --team ARI --defense

# Compute splits
python src/process.py --team ARI --all
```

### 4. Add another team
```bash
python src/fetch.py --team LAD --type all
python src/load_db.py --team LAD --all        # load roster into DB before fetch_value
python src/fetch_value.py --team LAD
python src/load_db.py --team LAD --value
python src/fetch_defense.py --team LAD
python src/load_db.py --team LAD --defense
python src/process.py --team LAD --all
```

### 5. Start the web server
```bash
python app/app.py
```

Then open [http://localhost:5000](http://localhost:5000).

---

## URL Structure

| URL | Page |
|---|---|
| `/` | Team picker homepage |
| `/<team>` | Team roster (e.g. `/ari`, `/lad`, `/sf`, `/sd`, `/col`) |
| `/<team>/payroll` | Team payroll page |
| `/batter/<id>` | Batter profile (player IDs are globally unique) |
| `/pitcher/<id>` | Pitcher profile |
| `/payroll` | Redirects to `/ari/payroll` (legacy) |

---

## Data Pipeline

```
fetch.py         -->  roster_{TEAM}_{SEASON}.csv, statcast_{TEAM}_{SEASON}.csv
fetch_value.py   -->  value_{TEAM}_{SEASON}.csv, awards_{TEAM}_{SEASON}.csv
fetch_defense.py -->  defense_{TEAM}_{SEASON}.csv
    |
load_db.py       -->  baseball.db (players, pitches, player_value, player_awards, player_defense)
    |
process.py       -->  baseball.db (batter_splits, pitcher_splits)
```

**Split dimensions:** overall, inning, count, runners, outs, handedness, venue (home/away), stadium, pitch type, score state, leverage, month, opponent team, opponent division, opponent league

---

## Data Notes

- **Team codes**: `src/teams.py` maps every team to its MLB API ID, Statcast code, FanGraphs code, Spotrac slug, division, and league. Statcast stores Arizona as `AZ` (not `ARI`).
- **Database**: Single `baseball.db` with a `team` column on all tables. The `pitches` table is shared (no team column — uses `home_team`/`away_team`).
- **Pitcher classification**: SP vs RP determined dynamically — pitcher who started in inning 1 in 3+ games = SP.
- **ERA/WHIP/IP**: Computed from Statcast event data. Runs = post_bat_score − bat_score changes on terminal PAs (approximation — all runs, not just earned). IP uses standard baseball X.Y convention (e.g. 6.2 = 6⅔ innings).
- **WAR**: Full-season fWAR from FanGraphs. Players who split time between teams show combined season totals.
- **Salary**: Scraped from Spotrac. Players on minor league deals may not appear.
- **$/WAR**: Calculated only for WAR > 0. 0 WAR = ∞, negative WAR shown as the raw value.
- **Two-way players** (e.g. Shohei Ohtani): shown on both batter and pitcher tabs of their team roster.

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
| `GET /api/batter/<id>/defense` | Fielding stats + sprint speed |
| `GET /api/pitcher/<id>/defense` | Sprint speed |
| `GET /api/search/players?q=<name>&type=<pitcher\|batter>` | Player name search (proxied from MLB Stats API) |
| `GET /sitemap.xml` | Auto-generated sitemap |

**Filter params:** `inning`, `balls`, `strikes`, `p_throws`, `stand`, `runners`, `home_away`, `outs`, `pitch_type`, `opponent_id`

---

*Data sourced from Baseball Savant, FanGraphs, Spotrac, and MLB Stats API. Not affiliated with MLB or any MLB team.*
