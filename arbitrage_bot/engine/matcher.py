import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Kalshi uses short abbreviations; map them to keywords that appear in PS3838 names
ABBREV_MAP = {
    # NBA & MLB Combined where needed
    "LAL": ["los angeles lakers", "lakers", "la lakers"],
    "HOU": ["houston rockets", "houston", "rockets"],
    "BOS": ["boston celtics", "boston", "celtics"],
    "MIA": ["miami heat", "miami", "heat"],
    "NYK": ["new york knicks", "knicks"],
    "PHI": ["philadelphia 76ers", "76ers", "sixers", "philadelphia", "philadelphia phillies", "phillies"], # Merged
    "MIL": ["milwaukee bucks", "milwaukee", "bucks", "milwaukee brewers", "brewers"], # Merged fix
    "CLE": ["cleveland cavaliers", "cleveland", "cavaliers", "cleveland guardians", "guardians"], # Merged
    "IND": ["indiana pacers", "indiana", "pacers"],
    "ORL": ["orlando magic", "orlando", "magic"],
    "DET": ["detroit pistons", "detroit", "pistons", "detroit tigers", "tigers"], # Merged
    "TOR": ["toronto raptors", "toronto", "raptors"],
    "CHI": ["chicago bulls", "chicago", "bulls", "chicago cubs", "cubs", "chicago white sox", "white sox"], # Merged
    "ATL": ["atlanta hawks", "atlanta", "hawks"],
    "CHA": ["charlotte hornets", "charlotte", "hornets"],
    "WAS": ["washington wizards", "washington", "wizards"],
    "WSH": ["washington nationals", "nationals"],
    "BKN": ["brooklyn nets", "brooklyn", "nets"], # Fixed from NYN to BKN
    "GSW": ["golden state warriors", "golden state", "warriors"],
    "LAC": ["los angeles clippers", "clippers"],
    "PHX": ["phoenix suns", "phoenix", "suns"],
    "SAC": ["sacramento kings", "sacramento", "kings"],
    "UTA": ["utah jazz", "utah", "jazz"],
    "POR": ["portland trail blazers", "portland"],
    "DEN": ["denver nuggets", "denver", "nuggets"],
    "MIN": ["minnesota timberwolves", "minnesota"],
    "OKC": ["oklahoma city thunder", "oklahoma city"],
    "NOP": ["new orleans pelicans", "new orleans"],
    "SAS": ["san antonio spurs", "san antonio"],
    "DAL": ["dallas mavericks", "dallas", "mavericks"],
    "MEM": ["memphis grizzlies", "memphis"],
    # MLB
    "NYY": ["new york yankees", "yankees"],
    "ATH": ["athletics", "oakland athletics", "as"],
    "SEA": ["seattle mariners", "mariners"],
    "LAD": ["los angeles dodgers", "dodgers"],
    "SF":  ["san francisco giants", "giants"],
    "SD":  ["san diego padres", "padres"],
    "COL": ["colorado rockies", "rockies"],
    "AZ":  ["arizona diamondbacks", "diamondbacks"],
    "TEX": ["texas rangers", "rangers"],
    "KC":  ["kansas city royals", "royals"],
    "STL": ["st louis cardinals", "cardinals"],
    "CIN": ["cincinnati reds", "reds"],
    "PIT": ["pittsburgh pirates", "pirates"],
    "NYM": ["new york mets", "mets"],
    "TB":  ["tampa bay rays", "rays"],
    "BAL": ["baltimore orioles", "orioles"],
    "LAA": ["los angeles angels", "angels"],
    # EPL
    "LFC": ["liverpool"],
    "MCI": ["manchester city", "man city"],
    "ARS": ["arsenal"],
    "CFC": ["chelsea"],
    "MUN": ["manchester united", "man utd", "man united"],
    "TOT": ["tottenham", "spurs"],
    "NEW": ["newcastle"],
    "AVL": ["aston villa"],
    "NFO": ["nottingham"],
    "WHU": ["west ham"],
    "BRE": ["brentford"],
    "FUL": ["fulham"],
    "BOU": ["bournemouth"],
    "WOL": ["wolverhampton", "wolves"],
    "CRY": ["crystal palace"],
    "BRI": ["brighton"],
    "EVE": ["everton"],
    "LEE": ["leeds"],
    "BUR": ["burnley"],
    "SUN": ["sunderland"],
    # La Liga
    "BAR": ["barcelona"],
    "RMA": ["real madrid"],
    "ATM": ["atletico madrid", "atletico"],
    "SEV": ["sevilla"],
    "VIL": ["villarreal"],
    "RCC": ["celta vigo", "celta"],
    "GET": ["getafe"],
    "ESP": ["espanyol"],
    "OSA": ["osasuna"],
    "GIR": ["girona"],
    "RBB": ["real betis", "betis"],
    "VCF": ["valencia"],
    "ALA": ["alaves"],
    "MAL": ["mallorca"],
    # Bundesliga
    "BMU": ["bayern munich", "bayern"],
    "BVB": ["borussia dortmund", "dortmund"],
    "LEV": ["bayer leverkusen", "leverkusen"],
    "RBL": ["rb leipzig", "leipzig"],
    "SGE": ["eintracht frankfurt", "frankfurt"],
    "BMG": ["borussia monchengladbach", "gladbach"],
    "VFB": ["stuttgart"],
    "TSG": ["hoffenheim"],
    "SCF": ["freiburg"],
    "UNI": ["union berlin"],
    "STP": ["st pauli"],
    "M05": ["mainz"],
    "SVW": ["werder bremen", "bremen"],
    "WOB": ["wolfsburg"],
    "FCA": ["augsburg"],
    "KOE": ["cologne", "koln"],
    "FCH": ["heidenheim"],
    # Serie A
    "INT": ["inter milan", "inter"],
    "ACM": ["ac milan", "milan"],
    "JUV": ["juventus"],
    "NAP": ["napoli"],
    "ROM": ["roma"],
    "LAZ": ["lazio"],
    "FIO": ["fiorentina"],
    "ATA": ["atalanta"],
    "BFC": ["bologna"],
    "UDI": ["udinese"],
    "GEN": ["genoa"],
    "COM": ["como"],
    "CAG": ["cagliari"],
    "PAR": ["parma"],
    "VER": ["hellas verona", "verona"],
    "LEC": ["lecce"],
}

_STRIP  = re.compile(r"[^a-z0-9 ]")
_SPACES = re.compile(r"\s+")
_STOP   = {"vs", "at", "the", "de", "del", "la", "le", "fc", "sc", "bc", "ac"}
_STRONG_FILTERS = {"u19", "u21", "u23", "women", "corners", "women's", "youth"}


def _normalize(name: str) -> set:
    s = name.lower()
    s = _STRIP.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    return {t for t in s.split() if t not in _STOP and len(t) > 1}


def _get_special_tags(name: str) -> set:
    s = name.lower()
    return {tag for tag in _STRONG_FILTERS if tag in s}


def _expand_abbrevs(tokens: set) -> set:
    expanded = set(tokens)
    for token in tokens:
        upper = token.upper()
        if upper in ABBREV_MAP:
            for alias in ABBREV_MAP[upper]:
                expanded.update(alias.split())
    return expanded


def _score(ps_home: str, ps_away: str, kalshi_title: str) -> float:
    # Check for special tags mismatch (U19, Women, Corners)
    ps_combined = f"{ps_home} {ps_away}".lower()
    k_lower = kalshi_title.lower()

    ps_tags = _get_special_tags(ps_combined)
    k_tags = _get_special_tags(k_lower)

    if ps_tags != k_tags:
        # If one has "women" and the other doesn't, it's a mismatch
        return 0.0

    k_tokens_raw = _normalize(kalshi_title)
    k_tokens = _expand_abbrevs(k_tokens_raw)
    if not k_tokens:
        return 0.0

    home_tokens = _normalize(ps_home)
    away_tokens = _normalize(ps_away)

    home_overlap = home_tokens & k_tokens
    away_overlap = away_tokens & k_tokens

    # Both teams must match something — prevents partial team match false positives
    if not home_overlap or not away_overlap:
        return 0.0

    # Avoid matching if home and away overlap with the SAME tokens in Kalshi
    if home_overlap == away_overlap and len(home_overlap) == 1:
        token = list(home_overlap)[0]
        if token in ("new", "york", "los", "angeles", "chicago", "st", "louis"):
            return 0.0

    ps_tokens = home_tokens | away_tokens
    return len(home_overlap | away_overlap) / max(len(ps_tokens), len(k_tokens_raw))


def find_best_kalshi_match(
    ps_home: str,
    ps_away: str,
    kalshi_events: list,
    min_score: float = 0.35,
    ps_league: str = "",
) -> Optional[dict]:
    best_score = min_score
    best_event = None

    for event in kalshi_events:
        # League check (simple heuristic)
        if ps_league:
            k_title = event.get('title', '').lower()
            ps_l_lower = ps_league.lower()
            # If leagues are clearly different (e.g. NBA vs WNBA, EPL vs Championship)
            if "nba" in ps_l_lower and "wnba" in k_title: continue
            if "wnba" in ps_l_lower and "nba" in k_title and "wnba" not in k_title: continue

        combined = f"{event.get('title', '')} {event.get('sub_title', '')}"
        score = _score(ps_home, ps_away, combined)
        if score > best_score:
            best_score = score
            best_event = event

    if best_event:
        logger.debug(
            "Matched '%s vs %s' → '%s' (%.2f)",
            ps_home, ps_away, best_event.get("sub_title"), best_score,
        )
    return best_event
