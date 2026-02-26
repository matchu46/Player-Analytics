"""
teams.py — Central team configuration for all 30 MLB teams.

Import this module everywhere instead of scattered constants.

Usage:
    from teams import TEAMS, SEASON, get_team
    cfg = get_team('ARI')   # returns dict with team metadata
"""

SEASON = 2025

# ---------------------------------------------------------------------------
# All 30 MLB teams
# Keys:
#   mlb_team_id   - MLB Stats API teamId
#   statcast_code - home_team column value in Statcast / pitches table
#   fg_code       - FanGraphs / pybaseball team code
#   spotrac_slug  - Spotrac URL slug for payroll scraping
#   full_name     - Full official team name
#   short_name    - Short nickname for UI labels
#   season_start  - Statcast pull start date (regular season)
#   season_end    - Statcast pull end date
# ---------------------------------------------------------------------------

TEAMS: dict[str, dict] = {
    'ARI': {
        'mlb_team_id':   109,
        'statcast_code': 'AZ',
        'fg_code':       'ARI',
        'spotrac_slug':  'arizona-diamondbacks',
        'full_name':     'Arizona Diamondbacks',
        'short_name':    'D-backs',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#A71930',  # Sedona Red
        'secondary_color': '#E3D4AD',  # Sand
    },
    'ATL': {
        'mlb_team_id':   144,
        'statcast_code': 'ATL',
        'fg_code':       'ATL',
        'spotrac_slug':  'atlanta-braves',
        'full_name':     'Atlanta Braves',
        'short_name':    'Braves',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#CE1141',  # Red
        'secondary_color': '#EAAA00',  # Gold
    },
    'BAL': {
        'mlb_team_id':   110,
        'statcast_code': 'BAL',
        'fg_code':       'BAL',
        'spotrac_slug':  'baltimore-orioles',
        'full_name':     'Baltimore Orioles',
        'short_name':    'Orioles',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#DF4601',  # Orange
        'secondary_color': '#F5E03A',  # Yellow
    },
    'BOS': {
        'mlb_team_id':   111,
        'statcast_code': 'BOS',
        'fg_code':       'BOS',
        'spotrac_slug':  'boston-red-sox',
        'full_name':     'Boston Red Sox',
        'short_name':    'Red Sox',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#BD3039',  # Red
        'secondary_color': '#C8DFF4',  # Light Blue
    },
    'CHC': {
        'mlb_team_id':   112,
        'statcast_code': 'CHC',
        'fg_code':       'CHC',
        'spotrac_slug':  'chicago-cubs',
        'full_name':     'Chicago Cubs',
        'short_name':    'Cubs',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#0E3386',  # Blue
        'secondary_color': '#CC3433',  # Red
    },
    'CWS': {
        'mlb_team_id':   145,
        'statcast_code': 'CWS',
        'fg_code':       'CWS',
        'spotrac_slug':  'chicago-white-sox',
        'full_name':     'Chicago White Sox',
        'short_name':    'White Sox',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#C4CED4',  # Silver
        'secondary_color': '#FFFFFF',  # White
    },
    'CIN': {
        'mlb_team_id':   113,
        'statcast_code': 'CIN',
        'fg_code':       'CIN',
        'spotrac_slug':  'cincinnati-reds',
        'full_name':     'Cincinnati Reds',
        'short_name':    'Reds',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#C6011F',  # Red
        'secondary_color': '#F5E4C8',  # Cream
    },
    'CLE': {
        'mlb_team_id':   114,
        'statcast_code': 'CLE',
        'fg_code':       'CLE',
        'spotrac_slug':  'cleveland-guardians',
        'full_name':     'Cleveland Guardians',
        'short_name':    'Guardians',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#E31937',  # Red
        'secondary_color': '#D8C08C',  # Gold/Tan
    },
    'COL': {
        'mlb_team_id':   115,
        'statcast_code': 'COL',
        'fg_code':       'COL',
        'spotrac_slug':  'colorado-rockies',
        'full_name':     'Colorado Rockies',
        'short_name':    'Rockies',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#7B2FBE',  # Purple (lightened for visibility)
        'secondary_color': '#C4CED4',  # Silver
    },
    'DET': {
        'mlb_team_id':   116,
        'statcast_code': 'DET',
        'fg_code':       'DET',
        'spotrac_slug':  'detroit-tigers',
        'full_name':     'Detroit Tigers',
        'short_name':    'Tigers',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#FA4616',  # Orange
        'secondary_color': '#D7D3C6',  # Light Gray
    },
    'HOU': {
        'mlb_team_id':   117,
        'statcast_code': 'HOU',
        'fg_code':       'HOU',
        'spotrac_slug':  'houston-astros',
        'full_name':     'Houston Astros',
        'short_name':    'Astros',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#EB6E1F',  # Orange
        'secondary_color': '#D8C08C',  # Gold/Tan
    },
    'KC': {
        'mlb_team_id':   118,
        'statcast_code': 'KC',
        'fg_code':       'KC',
        'spotrac_slug':  'kansas-city-royals',
        'full_name':     'Kansas City Royals',
        'short_name':    'Royals',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#004687',  # Blue
        'secondary_color': '#C09A5B',  # Gold
    },
    'LAA': {
        'mlb_team_id':   108,
        'statcast_code': 'LAA',
        'fg_code':       'LAA',
        'spotrac_slug':  'los-angeles-angels',
        'full_name':     'Los Angeles Angels',
        'short_name':    'Angels',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#BA0021',  # Red
        'secondary_color': '#C4CED4',  # Silver
    },
    'LAD': {
        'mlb_team_id':   119,
        'statcast_code': 'LAD',
        'fg_code':       'LAD',
        'spotrac_slug':  'los-angeles-dodgers',
        'full_name':     'Los Angeles Dodgers',
        'short_name':    'Dodgers',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#005A9C',  # Dodger Blue
        'secondary_color': '#FFFFFF',  # White
    },
    'MIA': {
        'mlb_team_id':   146,
        'statcast_code': 'MIA',
        'fg_code':       'MIA',
        'spotrac_slug':  'miami-marlins',
        'full_name':     'Miami Marlins',
        'short_name':    'Marlins',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#00A3E0',  # Blue
        'secondary_color': '#FF6600',  # Orange
    },
    'MIL': {
        'mlb_team_id':   158,
        'statcast_code': 'MIL',
        'fg_code':       'MIL',
        'spotrac_slug':  'milwaukee-brewers',
        'full_name':     'Milwaukee Brewers',
        'short_name':    'Brewers',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#FFC52F',  # Gold
        'secondary_color': '#B0C4D4',  # Light Blue
    },
    'MIN': {
        'mlb_team_id':   142,
        'statcast_code': 'MIN',
        'fg_code':       'MIN',
        'spotrac_slug':  'minnesota-twins',
        'full_name':     'Minnesota Twins',
        'short_name':    'Twins',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#D31145',  # Red
        'secondary_color': '#B8CBE4',  # Light Blue
    },
    'NYM': {
        'mlb_team_id':   121,
        'statcast_code': 'NYM',
        'fg_code':       'NYM',
        'spotrac_slug':  'new-york-mets',
        'full_name':     'New York Mets',
        'short_name':    'Mets',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#0055A4',  # Blue
        'secondary_color': '#FF5910',  # Orange
    },
    'NYY': {
        'mlb_team_id':   147,
        'statcast_code': 'NYY',
        'fg_code':       'NYY',
        'spotrac_slug':  'new-york-yankees',
        'full_name':     'New York Yankees',
        'short_name':    'Yankees',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#003087',  # Navy
        'secondary_color': '#C4CED4',  # Silver
    },
    'ATH': {
        'mlb_team_id':   133,
        'statcast_code': 'ATH',
        'fg_code':       'ATH',
        'spotrac_slug':  'oakland-athletics',
        'full_name':     'Athletics',
        'short_name':    'Athletics',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#EFB21E',  # Gold
        'secondary_color': '#B5CFBB',  # Light Green
    },
    'PHI': {
        'mlb_team_id':   143,
        'statcast_code': 'PHI',
        'fg_code':       'PHI',
        'spotrac_slug':  'philadelphia-phillies',
        'full_name':     'Philadelphia Phillies',
        'short_name':    'Phillies',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#E81828',  # Red
        'secondary_color': '#C8D8E8',  # Light Blue
    },
    'PIT': {
        'mlb_team_id':   134,
        'statcast_code': 'PIT',
        'fg_code':       'PIT',
        'spotrac_slug':  'pittsburgh-pirates',
        'full_name':     'Pittsburgh Pirates',
        'short_name':    'Pirates',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#FDB827',  # Gold
        'secondary_color': '#E8E8E8',  # Silver/White
    },
    'SD': {
        'mlb_team_id':   135,
        'statcast_code': 'SD',
        'fg_code':       'SD',
        'spotrac_slug':  'san-diego-padres',
        'full_name':     'San Diego Padres',
        'short_name':    'Padres',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#C4A882',  # Sand/Tan
        'secondary_color': '#FFC425',  # Gold
    },
    'SF': {
        'mlb_team_id':   137,
        'statcast_code': 'SF',
        'fg_code':       'SF',
        'spotrac_slug':  'san-francisco-giants',
        'full_name':     'San Francisco Giants',
        'short_name':    'Giants',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#FD5A1E',  # Giants Orange
        'secondary_color': '#E8E8E8',  # Light Gray
    },
    'SEA': {
        'mlb_team_id':   136,
        'statcast_code': 'SEA',
        'fg_code':       'SEA',
        'spotrac_slug':  'seattle-mariners',
        'full_name':     'Seattle Mariners',
        'short_name':    'Mariners',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#005C5C',  # Teal
        'secondary_color': '#C4CED4',  # Silver
    },
    'STL': {
        'mlb_team_id':   138,
        'statcast_code': 'STL',
        'fg_code':       'STL',
        'spotrac_slug':  'st-louis-cardinals',
        'full_name':     'St. Louis Cardinals',
        'short_name':    'Cardinals',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#C41E3A',  # Red
        'secondary_color': '#FEDB00',  # Gold
    },
    'TB': {
        'mlb_team_id':   139,
        'statcast_code': 'TB',
        'fg_code':       'TB',
        'spotrac_slug':  'tampa-bay-rays',
        'full_name':     'Tampa Bay Rays',
        'short_name':    'Rays',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#092C5C',  # Navy
        'secondary_color': '#8FBCE6',  # Light Blue
    },
    'TEX': {
        'mlb_team_id':   140,
        'statcast_code': 'TEX',
        'fg_code':       'TEX',
        'spotrac_slug':  'texas-rangers',
        'full_name':     'Texas Rangers',
        'short_name':    'Rangers',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#003278',  # Navy
        'secondary_color': '#C8D8F5',  # Light Blue
    },
    'TOR': {
        'mlb_team_id':   141,
        'statcast_code': 'TOR',
        'fg_code':       'TOR',
        'spotrac_slug':  'toronto-blue-jays',
        'full_name':     'Toronto Blue Jays',
        'short_name':    'Blue Jays',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#134A8E',  # Blue
        'secondary_color': '#E8291C',  # Red
    },
    'WSH': {
        'mlb_team_id':   120,
        'statcast_code': 'WSH',
        'fg_code':       'WSH',
        'spotrac_slug':  'washington-nationals',
        'full_name':     'Washington Nationals',
        'short_name':    'Nationals',
        'season_start':  '2025-03-27',
        'season_end':    '2025-10-01',
        'primary_color': '#AB0003',  # Red
        'secondary_color': '#D4B483',  # Gold/Tan
    },
}


def get_team(code: str) -> dict:
    """Return team config dict for the given code, or raise KeyError."""
    return TEAMS[code.upper()]
