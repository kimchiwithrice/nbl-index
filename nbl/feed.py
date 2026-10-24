"""Fetches finished games that nblR hasn't added yet, straight from the NBL match centre feed.

Rows are written in nblR's column format to extra_*.csv, which data.load() merges in. If the feed
fails or looks wrong, the game is skipped and the build carries on with nblR's data.
"""
import json
import os

import pandas as pd
import requests

MATCH_URL = "https://prod.rosetta.nbl.com.au/get/match/{}/live/all"
HEADERS = {
    "accept": "*/*",
    "origin": "https://nbl.com.au",
    "referer": "https://nbl.com.au/",
    "user-agent": "Mozilla/5.0 (compatible; nblindex.com stats site)",
}
EXTRA_FILES = {"results": "extra_results.csv", "box_team": "extra_box_team.csv", "box_player": "extra_box_player.csv"}
MAX_GAMES = 30          # per build, so a long nblR outage doesn't slow the build down
WAIT_HOURS = 2.5        # don't ask for a game until it should be over


def shape(obj, depth=0):
    """Keys and types only (no values), for the build log when the feed doesn't look as expected."""
    pad = "  " * depth
    if isinstance(obj, dict):
        return "\n".join(f"{pad}{k}: {type(v).__name__}" + ("\n" + shape(v, depth + 1) if isinstance(v, (dict, list)) and depth < 2 else "")
                         for k, v in obj.items())
    if isinstance(obj, list):
        return f"{pad}[{len(obj)} items]" + ("\n" + shape(obj[0], depth + 1) if obj and depth < 2 else "")
    return pad + type(obj).__name__


def _id(ref):
    """A nested reference can be a plain id or an object with an id."""
    if isinstance(ref, dict):
        return str(ref.get("id", ""))
    return str(ref) if ref is not None else ""


def _num(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return float("nan")


def _field(row, nested, key):
    """Read `key` from the row itself, or from a nested object such as row['player']."""
    if row.get(key) not in (None, ""):
        return row.get(key)
    inner = row.get(nested)
    return inner.get(key) if isinstance(inner, dict) else None


def _minutes(v):
    """Feed minutes come as 'MM:SS', seconds, or decimal minutes. Return 'MM:SS' like nblR."""
    if v in (None, ""):
        return ""
    if isinstance(v, str) and ":" in v:
        return v
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    mins = x / 60 if x > 60 else x  # seconds if it can't be minutes in one game
    return f"{int(mins):02d}:{int(round((mins % 1) * 60)):02d}"


def parse(data, season):
    """Turn one match from the feed into nblR-style rows. Returns (result, team_rows, player_rows)."""
    home, away = data.get("home_team") or {}, data.get("away_team") or {}
    side = {_id(home): ("home", home, away), _id(away): ("away", away, home)}
    if "" in side or len(side) != 2:
        raise ValueError("couldn't identify both teams")

    team_stats = data.get("team_match_statistics") or []
    totals = {}
    periods = set()
    for row in team_stats:
        period = str(row.get("period", ""))
        periods.add(period)
        tid = _id(row.get("team"))
        if period == "0" and tid in side:
            totals[tid] = row
    if len(totals) != 2:
        raise ValueError(f"full-game team totals not found (got {len(totals)})")
    overtimes = sum(1 for p in periods if p.isdigit() and int(p) > 4)

    team_rows = []
    for tid, row in totals.items():
        ha, me, opp = side[tid]
        pts = _num(row, "points")
        opp_pts = _num(totals[_id(opp)], "points")
        team_rows.append({
            "match_id": data["id"], "season": season, "home_away": ha,
            "name": me.get("name"), "short_name": me.get("team_code"), "code": me.get("team_code"),
            "score": pts, "full_score": pts, "opp_name": opp.get("name"), "opp_short_name": opp.get("team_code"),
            "opp_score": opp_pts, "opp_full_score": opp_pts, "minutes": _num(row, "minutes"),
            "field_goals_made": _num(row, "field_goals_made"), "field_goals_attempted": _num(row, "field_goals_attempted"),
            "three_pointers_made": _num(row, "three_points_made", "three_pointers_made"),
            "three_pointers_attempted": _num(row, "three_points_attempted", "three_pointers_attempted"),
            "two_pointers_made": _num(row, "two_points_made", "two_pointers_made"),
            "two_pointers_attempted": _num(row, "two_points_attempted", "two_pointers_attempted"),
            "free_throws_made": _num(row, "free_throws_made"), "free_throws_attempted": _num(row, "free_throws_attempted"),
            "rebounds_defensive": _num(row, "defensive_rebounds", "rebounds_defensive"),
            "rebounds_offensive": _num(row, "offensive_rebounds", "rebounds_offensive"),
            "rebounds_total": _num(row, "rebounds", "rebounds_total"), "assists": _num(row, "assists"),
            "turnovers": _num(row, "turnovers"), "steals": _num(row, "steals"), "blocks": _num(row, "blocks"),
            "fouls_personal": _num(row, "personal_fouls", "fouls_personal"), "points": pts,
        })

    player_rows = []
    for row in data.get("player_match_statistics") or []:
        if str(row.get("period", "0")) != "0":
            continue
        tid = _id(row.get("team"))
        if tid not in side:
            continue
        ha, me, opp = side[tid]
        player_rows.append({
            "match_id": data["id"], "season": season, "team_name": me.get("name"),
            "home_away": 1 if ha == "home" else 2, "opp_name": opp.get("name"), "opp_short_name": opp.get("team_code"),
            "player_id": _id(row.get("player")), "first_name": _field(row, "player", "first_name"),
            "family_name": _field(row, "player", "last_name") or _field(row, "player", "family_name"),
            "playing_position": _field(row, "player", "playing_position"),
            "starter": _num(row, "starter"), "shirt_number": _field(row, "player", "jersey_number"),
            "minutes": _minutes(row.get("minutes")), "points": _num(row, "points"),
            "field_goals_made": _num(row, "field_goals_made"), "field_goals_attempted": _num(row, "field_goals_attempted"),
            "three_pointers_made": _num(row, "three_points_made", "three_pointers_made"),
            "three_pointers_attempted": _num(row, "three_points_attempted", "three_pointers_attempted"),
            "two_pointers_made": _num(row, "two_points_made", "two_pointers_made"),
            "two_pointers_attempted": _num(row, "two_points_attempted", "two_pointers_attempted"),
            "free_throws_made": _num(row, "free_throws_made"), "free_throws_attempted": _num(row, "free_throws_attempted"),
            "rebounds_defensive": _num(row, "defensive_rebounds", "rebounds_defensive"),
            "rebounds_offensive": _num(row, "offensive_rebounds", "rebounds_offensive"),
            "rebounds_total": _num(row, "rebounds", "rebounds_total"), "assists": _num(row, "assists"),
            "turnovers": _num(row, "turnovers"), "steals": _num(row, "steals"), "blocks": _num(row, "blocks"),
            "fouls_personal": _num(row, "personal_fouls", "fouls_personal"), "plus_minus": _num(row, "plus_minus"),
            "efficiency": _num(row, "efficiency"), "active": _num(row, "participated", "active"),
        })

    # Sanity checks: totals have to add up before we trust anything.
    by_side = {r["home_away"]: r for r in team_rows}
    for r in team_rows:
        if not (r["field_goals_attempted"] > 20 and r["points"] > 30):
            raise ValueError("team totals look incomplete")
    for ha, code in (("home", 1), ("away", 2)):
        player_pts = sum(p["points"] for p in player_rows if p["home_away"] == code and p["points"] == p["points"])
        if player_rows and abs(player_pts - by_side[ha]["points"]) > 0.5:
            raise ValueError(f"player points ({player_pts:.0f}) don't add up to the team's")

    result = {"match_id": data["id"], "match_status": "COMPLETE", "extra_periods_used": overtimes,
              "home_score_string": str(int(by_side["home"]["points"])),
              "away_score_string": str(int(by_side["away"]["points"]))}
    return result, team_rows, player_rows


def keep(data_dir, season, match_id, data):
    """Save the feed's answer as it came, for the data archive."""
    folder = os.path.join(data_dir, "feed", str(season))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{match_id}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)


def fill_missing(data_dir, paths, log=print):
    """Fetch finished games that nblR doesn't have yet. Writes extra_*.csv; returns how many it added."""
    out = {k: os.path.join(data_dir, v) for k, v in EXTRA_FILES.items()}
    for p in out.values():
        if os.path.exists(p):
            os.remove(p)
    try:
        results = pd.read_csv(paths["results"], low_memory=False)
        box = pd.read_csv(paths["box_team"], low_memory=False, usecols=["match_id"])
    except Exception as e:  # noqa: BLE001
        log(f"  fallback skipped: couldn't read nblR files ({e})")
        return 0
    results["utc"] = pd.to_datetime(results.match_time_utc.astype(str).str.replace("T", " ").str[:19],
                                    errors="coerce", utc=True)
    latest = results.season.dropna().astype(str).max()
    have = set(box.match_id.astype(str))
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=WAIT_HOURS)
    todo = results[(results.season.astype(str) == latest) & (results.utc < cutoff)
                   & ~results.match_id.astype(str).isin(have)].sort_values("utc").head(MAX_GAMES)
    if todo.empty:
        return 0

    log(f"  {len(todo)} finished game(s) missing from nblR; trying the NBL feed")
    got = {"results": [], "box_team": [], "box_player": []}
    shown_shape = False
    session = requests.Session()
    for row in todo.itertuples():
        data = None
        try:
            resp = session.get(MATCH_URL.format(row.match_id), headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            keep(data_dir, latest, row.match_id, data)
            data = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(data, list):
                data = data[0] if data else {}
            data.setdefault("id", row.match_id)
            if str(data.get("match_status", "")).lower() != "complete":
                log(f"    {row.match_id}: not final yet ({data.get('match_status')})")
                continue
            result, teams, players = parse(data, latest)
        except Exception as e:  # noqa: BLE001
            log(f"    {row.match_id}: skipped ({e})")
            if not shown_shape and isinstance(data, dict):
                log("    feed layout (keys only):\n" + shape(data))
                shown_shape = True
            continue
        got["results"].append(result)
        got["box_team"].extend(teams)
        got["box_player"].extend(players)
        log(f"    {row.match_id}: added ({len(players)} player rows)")

    if not got["results"]:
        return 0
    for key, rows in got.items():
        pd.DataFrame(rows).to_csv(out[key], index=False)
    return len(got["results"])
