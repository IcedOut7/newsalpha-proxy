"""
Core scanning logic — shared between CLI (main.py) and web UI (web_app.py).
"""

import re
import logging
from datetime import datetime
from typing import Optional

from .engine.matcher import find_best_kalshi_match, ABBREV_MAP, _normalize
from .engine.arb_detector import detect_arb
from .engine.stake_calc import calculate_stakes, polymarket_taker_fee

logger = logging.getLogger(__name__)

_odds_fingerprints: dict = {}
_match_cache: dict = {}

DEFAULT_SETTINGS = {
    "bankroll": 1000.0,
    "auto_execute": False,
    "max_exec_stake": 2.0,
    "exec_cooldown_sec": 0,
    "min_profit_pct": 0.5,
    "max_profit_pct_filter": 50.0,
    "min_odds": 1.3,
    "max_odds": 50.0,
    "arb_lifetime_min_sec": 5,
    "arb_lifetime_sec": 30,
    "leg2_wait_sec": 20,
    "max_loss_pct": -2.0,
    "arb_quality_min": 1,
    "max_identical_arbs_per_event": 3,
    "max_uncovered_per_event": 1,
    "poll_interval": 2,
    "kalshi_refresh_every": 60,
    "polymarket_enabled":   True,
    "kalshi_enabled":       True,
    "polymarket_min_stake": 1.0,
    "enable_soccer_moneyline": True,
    "enable_soccer_totals": True,
    "enable_spreads": True,
    "enable_totals": True,
}

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _get_full_match_period(event: dict):
    for p in event.get("periods", []):
        if p["number"] == 0 and p.get("moneyline"):
            return p
    return None


def _is_halftime(event: dict) -> bool:
    score = event.get("live_score", {})
    if not score:
        return False
    period = score.get("period", "")
    sport_id = event.get("sport_id")

    p_lower = str(period).lower()
    # Broaden detection for various providers
    ht_markers = {"ht", "halftime", "half time", "half", "intermission", "break", "45"}

    if sport_id == 29: # Soccer
        return p_lower in ht_markers
    return p_lower in ht_markers or p_lower == "2"


def _kalshi_event_date(sub_title: str) -> Optional[datetime]:
    m = re.search(r'\((\w+)\s+(\d+)\)', sub_title or "")
    if not m:
        return None
    month_str, day_str = m.group(1).lower(), int(m.group(2))
    month = _MONTH_MAP.get(month_str[:3])
    if not month:
        return None
    today = datetime.now()
    year = today.year if month >= today.month - 1 else today.year + 1
    try:
        return datetime(year, month, day_str)
    except ValueError:
        return None


def _ps3838_event_date(starts: str) -> Optional[datetime]:
    if not starts:
        return None
    try:
        return datetime.fromisoformat(starts.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _dates_match(ke: dict, ps_starts: str) -> bool:
    # Kalshi dates are often just day/month. PS3838 is full ISO.
    # Strictness: matches must be within 24 hours of each other
    k_date = _kalshi_event_date(ke.get("sub_title", ""))
    ps_date = _ps3838_event_date(ps_starts)

    if k_date is None or ps_date is None:
        return True # Fallback to name-only if dates missing

    diff = abs((k_date - ps_date).total_seconds())
    return diff < 86400 # 24 hours


def _odds_fingerprint(period: dict) -> str:
    ml   = period.get("moneyline") or {}
    tots = period.get("totals") or []
    return f"{ml.get('home')},{ml.get('away')},{ml.get('draw')},{len(tots)}"


def _kalshi_team_side(team_id: str, home: str, away: str):
    tid = team_id.upper()
    expanded_keywords = set()
    if tid in ABBREV_MAP:
        for alias in ABBREV_MAP[tid]:
            for w in alias.upper().split():
                expanded_keywords.add(w)
    else:
        expanded_keywords.add(tid)

    def _matches(team_name: str) -> bool:
        name_words = team_name.upper().split()
        for kw in expanded_keywords:
            for w in name_words:
                if w.startswith(kw) or kw.startswith(w) and len(kw) >= 3:
                    return True
        return False

    home_match = _matches(home)
    away_match = _matches(away)
    if home_match and not away_match:
        return "home"
    if away_match and not home_match:
        return "away"
    return None


_SPORT_ID_MAP = {
    "soccer": 29, "basketball": 4, "tennis": 33,
    "baseball": 3, "hockey": 8, "amfootball": 15,
    "rugby": 12, "volleyball": 18, "handball": 10,
    "tabletennis": 20, "badminton": 7,
}


def _arb_ok(arb, min_profit, max_profit, min_odds, max_odds) -> bool:
    return (
        arb is not None
        and min_profit <= arb.profit_pct <= max_profit
        and min_odds  <= arb.ps3838_odds <= max_odds
    )


async def scan_once(ps, kalshi_events: list, poly_events: list = None, settings: dict = None, kalshi_client=None, poly_client=None) -> list:
    global _odds_fingerprints, _match_cache
    if settings is None:
        settings = {}
    if poly_events is None:
        poly_events = []

    bankroll          = settings.get("bankroll",              DEFAULT_SETTINGS["bankroll"])
    min_profit        = settings.get("min_profit_pct",        DEFAULT_SETTINGS["min_profit_pct"])
    max_profit        = settings.get("max_profit_pct_filter", DEFAULT_SETTINGS["max_profit_pct_filter"])
    min_odds          = settings.get("min_odds",              DEFAULT_SETTINGS["min_odds"])
    max_odds          = settings.get("max_odds",              DEFAULT_SETTINGS["max_odds"])
    max_stake_global  = settings.get("max_stake",             0)
    enable_soccer_ml  = settings.get("enable_soccer_moneyline", True)
    enable_soccer_totals = settings.get("enable_soccer_totals", True)
    enable_spreads    = settings.get("enable_spreads",          True)
    enable_totals     = settings.get("enable_totals",           True)

    disabled_ps_ids: set = set()
    for sport_str, ps_id in _SPORT_ID_MAP.items():
        sport_cfg = settings.get("sports_config", {}).get(sport_str, {})
        if sport_cfg.get("enabled") is False:
            disabled_ps_ids.add(ps_id)

    ps_events = await ps.get_all_live_events()
    if not ps_events:
        logger.debug("No live events found on PS3838")
        return []

    ke_index = {}
    ke_keyword_index = {}
    for ke in kalshi_events:
        ke_index[(ke["event_ticker"], ke["market_type"])] = ke
        title_tokens = _normalize(ke.get("title", "") + " " + ke.get("sub_title", ""))
        for token in title_tokens:
            ke_keyword_index.setdefault(token, []).append(("kalshi", ke))

    # Include Polymarket in keyword index
    for pe in poly_events:
        title_tokens = _normalize(pe.get("question", ""))
        for token in title_tokens:
            ke_keyword_index.setdefault(token, []).append(("poly", pe))

    all_pairs = []
    meta_set  = set()
    poly_matches = []

    for ps_event in ps_events:
        if ps_event.get("sport_id") in disabled_ps_ids:
            continue
        period = _get_full_match_period(ps_event)
        if not period: continue

        home, away = ps_event["home"], ps_event["away"]
        ps_starts  = ps_event.get("starts", "")
        ml = period.get("moneyline", {})
        home_odds, away_odds = ml.get("home"), ml.get("away")
        draw_odds = ml.get("draw")

        ev_id = ps_event["id"]
        fp    = _odds_fingerprint(period)
        odds_changed = _odds_fingerprints.get(ev_id) != fp
        _odds_fingerprints[ev_id] = fp

        # Reset cache if odds changed or if it's been a while (placeholder for TTL)
        if odds_changed or ev_id not in _match_cache:
            home_tokens = _normalize(home)
            away_tokens = _normalize(away)
            candidates = set()
            for t in (home_tokens | away_tokens):
                if t in ke_keyword_index:
                    candidates.update(ke_keyword_index[t])

            league = ps_event.get("league", "")
            matches = []
            for platform, item in candidates:
                if platform == "kalshi":
                    if find_best_kalshi_match(home, away, [item], ps_league=league) and _dates_match(item, ps_starts):
                        matches.append(("kalshi", item))
                else: # poly
                    title = item.get("question", "")
                    if find_best_kalshi_match(home, away, [{"title": title, "sub_title": ""}], min_score=0.45, ps_league=league):
                        matches.append(("poly", item))
            _match_cache[ev_id] = matches

        matched_items = _match_cache[ev_id]
        for platform, item in matched_items:
            if platform == "kalshi":
                all_pairs.append((ps_event, item))
                meta_set.add((item["event_ticker"], item["market_type"], item.get("sport") == "soccer"))
            elif platform == "poly" and home_odds and away_odds:
                poly_matches.append((ps_event, item))

    if not all_pairs and not poly_matches:
        logger.info("Сканирование завершено: 0 совпадений (проверено %d матчей PS3838)", len(ps_events))
        return []

    logger.info("Найдено совпадений: Kalshi=%d, Poly=%d (проверено %d матчей PS3838)",
                len(all_pairs), len(poly_matches), len(ps_events))

    # Refresh prices for Kalshi
    fresh_prices = {}
    if kalshi_client and meta_set:
        fresh_prices = await kalshi_client.refresh_matched_prices(list(meta_set))

    opportunities = []

    # Process Polymarket Arbs
    if poly_client and poly_matches:
        for ps_event, pe in poly_matches:
            period = _get_full_match_period(ps_event)
            ml = period.get("moneyline", {})
            h_odds, a_odds = ml.get("home"), ml.get("away")
            home, away = ps_event["home"], ps_event["away"]

            for token in pe.get("tokens", []):
                p_price = token.get("price")
                if not p_price or p_price <= 0 or p_price >= 1: continue

                # Verify price from orderbook for accuracy if it looks like an arb
                outcome_name = token.get("outcome", "").lower()
                side = "home" if (outcome_name in home.lower() or home.lower() in outcome_name) else \
                       "away" if (outcome_name in away.lower() or away.lower() in outcome_name) else None

                if side:
                    ps_outcome, ps_odds = ("away", a_odds) if side == "home" else ("home", h_odds)
                    arb = detect_arb(
                        event_name=f"{home} vs {away} (Poly)",
                        ps3838_event_id=ps_event["id"],
                        ps3838_sport_id=ps_event["sport_id"],
                        ps3838_outcome=ps_outcome,
                        ps3838_odds=ps_odds,
                        ps3838_period=0,
                        ps3838_bet_type="moneyline",
                        kalshi_ticker=token.get("token_id", ""),
                        kalshi_side="yes",
                        kalshi_price=p_price,
                    )
                    if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                        # Re-verify price from CLOB orderbook to ensure it's not stale
                        try:
                            book = await poly_client.get_orderbook(token["token_id"])
                            asks = book.get("asks", [])
                            if asks:
                                real_p_price = float(asks[0]["price"])
                                arb.kalshi_price = real_p_price
                                # Recalculate profit
                                arb.implied_kalshi = real_p_price
                                arb.margin = arb.implied_ps + real_p_price
                                arb.profit_pct = (1.0 / arb.margin - 1.0) * 100.0

                                if not _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                                    continue
                        except Exception:
                            continue

                        s = calculate_stakes(arb, bankroll, max_ps_stake=period.get("max_moneyline", 0),
                                            global_max_stake=max_stake_global,
                                            fee_fn=polymarket_taker_fee)
                        opportunities.append((arb, s, pe, token))

    seen_ps_events = {}
    for ps_event, ke in all_pairs:
        ev_id = ps_event["id"]
        if ev_id not in seen_ps_events:
            period = _get_full_match_period(ps_event)
            ml     = period.get("moneyline", {}) if period else {}
            seen_ps_events[ev_id] = (period, ml)
        period, ml = seen_ps_events[ev_id]
        if not period:
            continue

        home      = ps_event["home"]
        away      = ps_event["away"]
        sport_id  = ps_event["sport_id"]
        is_soccer = (sport_id == 29)
        halftime  = _is_halftime(ps_event) if is_soccer else False
        home_odds = ml.get("home")
        away_odds = ml.get("away")
        draw_odds = ml.get("draw")

        max_ml  = period.get("max_moneyline", 0) or 0
        max_spr = period.get("max_spread",    0) or 0
        max_tot = period.get("max_total",     0) or 0

        mtype = ke["market_type"]
        sport = ke.get("sport", "")

        if mtype == "total_over" and sport == "soccer":
            if not enable_soccer_totals or not halftime:
                continue
        if mtype == "spread" and not enable_spreads:
            continue

        effective_markets = fresh_prices.get(ke["event_ticker"], ke["markets"])

        for k_market in effective_markets:
            k_price = k_market["entry_price"]
            k_type  = k_market["market_type"]
            team_id = k_market["team_id"]

            if k_price <= 0:
                continue

            if k_type == "moneyline":
                if is_soccer and not enable_soccer_ml:
                    continue

                side = _kalshi_team_side(team_id, home, away)
                if side is None:
                    continue

                if is_soccer and draw_odds:
                    # 1. Check Arb: Kalshi NO (Team A doesn't win) vs PS3838 Team A Win
                    k_no_price = k_market.get("no_ask")
                    ps_win_outcome, ps_win_odds = ("home", home_odds) if side == "home" else ("away", away_odds)

                    if k_no_price and ps_win_odds:
                        arb = detect_arb(
                            event_name=f"{home} vs {away} (K-NO vs PS-WIN)",
                            ps3838_event_id=ps_event["id"],
                            ps3838_sport_id=ps_event["sport_id"],
                            ps3838_outcome=ps_win_outcome,
                            ps3838_odds=ps_win_odds,
                            ps3838_period=0,
                            ps3838_bet_type="moneyline",
                            kalshi_ticker=k_market["ticker"],
                            kalshi_side="no",
                            kalshi_price=k_no_price,
                        )
                        if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                            stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_ml,
                                                     global_max_stake=max_stake_global)
                            opportunities.append((arb, stakes, ke, k_market))

                    # 2. Check Arb: Kalshi YES (Team wins) vs PS3838 Double Chance (Draw + Other Team)
                    other_odds = away_odds if side == "home" else home_odds
                    if draw_odds and other_odds:
                        dc_prob = (1.0 / draw_odds) + (1.0 / other_odds)
                        dc_odds = 1.0 / dc_prob

                        arb = detect_arb(
                            event_name=f"{home} vs {away} (K-YES vs PS-X2)",
                            ps3838_event_id=ps_event["id"],
                            ps3838_sport_id=ps_event["sport_id"],
                            ps3838_outcome="draw_or_away" if side == "home" else "home_or_draw",
                            ps3838_odds=dc_odds,
                            ps3838_period=0,
                            ps3838_bet_type="moneyline",
                            kalshi_ticker=k_market["ticker"],
                            kalshi_side="yes",
                            kalshi_price=k_price,
                        )
                        if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                            stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_ml,
                                                     global_max_stake=max_stake_global)
                            opportunities.append((arb, stakes, ke, k_market))

                    continue

                ps_outcome, ps_odds = ("away", away_odds) if side == "home" else ("home", home_odds)
                if not ps_odds:
                    continue

                arb = detect_arb(
                    event_name=f"{home} vs {away}",
                    ps3838_event_id=ps_event["id"],
                    ps3838_sport_id=ps_event["sport_id"],
                    ps3838_outcome=ps_outcome,
                    ps3838_odds=ps_odds,
                    ps3838_period=0,
                    ps3838_bet_type="moneyline",
                    kalshi_ticker=k_market["ticker"],
                    kalshi_side="yes",
                    kalshi_price=k_price,
                )
                if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                    stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_ml,
                                             global_max_stake=max_stake_global)
                    opportunities.append((arb, stakes, ke, k_market))

            elif k_type == "draw" and draw_odds:
                if not enable_soccer_ml:
                    continue
                arb = detect_arb(
                    event_name=f"{home} vs {away}",
                    ps3838_event_id=ps_event["id"],
                    ps3838_sport_id=ps_event["sport_id"],
                    ps3838_outcome="draw",
                    ps3838_odds=draw_odds,
                    ps3838_period=0,
                    ps3838_bet_type="moneyline",
                    kalshi_ticker=k_market["ticker"],
                    kalshi_side="yes",
                    kalshi_price=k_price,
                )
                if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                    stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_ml,
                                             global_max_stake=max_stake_global)
                    opportunities.append((arb, stakes, ke, k_market))

            elif k_type == "total_under":
                if not (k_market["is_soccer"] and halftime and enable_soccer_totals):
                    continue
                for t in period.get("totals", []):
                    ps_over_odds = t.get("over")
                    line = t.get("points")
                    if not ps_over_odds or not line:
                        continue
                    try:
                        k_line = float(k_market["team_id"])
                    except ValueError:
                        continue
                    if abs(k_line - float(line)) > 0.6:
                        continue
                    arb = detect_arb(
                        event_name=f"{home} vs {away} TOTAL {line} UNDER",
                        ps3838_event_id=ps_event["id"],
                        ps3838_sport_id=ps_event["sport_id"],
                        ps3838_outcome="over",
                        ps3838_odds=ps_over_odds,
                        ps3838_period=0,
                        ps3838_bet_type="total",
                        kalshi_ticker=k_market["ticker"],
                        kalshi_side="no",
                        kalshi_price=k_price,
                    )
                    if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                        stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_tot,
                                                 global_max_stake=max_stake_global)
                        opportunities.append((arb, stakes, ke, k_market))

            elif k_type == "total_over":
                if not enable_totals:
                    continue
                for t in period.get("totals", []):
                    ps_under_odds = t.get("under")
                    line = t.get("points")
                    if not ps_under_odds or not line:
                        continue
                    try:
                        k_line = float(team_id)
                    except ValueError:
                        continue
                    if abs(k_line - float(line)) > 0.6:
                        continue
                    arb = detect_arb(
                        event_name=f"{home} vs {away} TOTAL {line}",
                        ps3838_event_id=ps_event["id"],
                        ps3838_sport_id=ps_event["sport_id"],
                        ps3838_outcome="under",
                        ps3838_odds=ps_under_odds,
                        ps3838_period=0,
                        ps3838_bet_type="total",
                        kalshi_ticker=k_market["ticker"],
                        kalshi_side="yes",
                        kalshi_price=k_price,
                    )
                    if _arb_ok(arb, min_profit, max_profit, min_odds, max_odds):
                        stakes = calculate_stakes(arb, bankroll, max_ps_stake=max_tot,
                                                 global_max_stake=max_stake_global)
                        opportunities.append((arb, stakes, ke, k_market))

    return opportunities


def _guess_sport(sport_id: int) -> str:
    _MAP = {29: "Soccer", 4: "Basketball", 3: "Baseball", 8: "Hockey",
            15: "Football", 6: "Boxing", 7: "MMA", 33: "Tennis"}
    return _MAP.get(sport_id, "")
