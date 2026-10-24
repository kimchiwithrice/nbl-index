"""Checks that each data source still works, so a broken one gets noticed.

Each build writes what it found to data/health.json. alert.py reads it after the build and opens
a GitHub issue for any source that has been broken two builds in a row, then closes the issue
once the source works again.

Sources:
    nblr  nblR's results and box score files
    pbp   nblR's play-by-play file
    feed  the NBL match centre feed (the fallback for games nblR doesn't have yet)
"""
import json
import os

import pandas as pd
import requests

from . import feed, rds

BEHIND_HOURS = 72  # nblR usually catches up within a day; this long means it has stopped

# Columns the build reads from each file. If one goes missing the file has changed shape.
COLUMNS = {
    "results": ["match_id", "season", "match_status", "match_type", "match_time_utc", "home_team_name",
                "away_team_name", "home_score_string", "away_score_string", "at_neutral_venue",
                "extra_periods_used", "round_number", "venue_name"],
    "box_team": ["match_id", "home_away", "name", "score", "points", "field_goals_attempted",
                 "rebounds_offensive", "turnovers", "free_throws_attempted"],
    "box_player": ["match_id", "season", "team_name", "opp_name", "first_name", "family_name", "starter",
                   "minutes", "seconds", "points", "plus_minus", "plus_minus_points"],
}

_path = None
_state = {}


def start(data_dir):
    global _path, _state
    _path = os.path.join(data_dir, "health.json")
    _state = {"checked": f"{pd.Timestamp.now(tz='UTC'):%d %b %Y %H:%M} UTC", "problems": {}, "ok": []}
    _save()


def problem(source, text):
    _state.setdefault("problems", {}).setdefault(source, []).append(text)
    _save()


def ok(source):
    if source not in _state.get("problems", {}) and source not in _state.get("ok", []):
        _state.setdefault("ok", []).append(source)
        _save()


def _save():
    if _path:
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=1)


def check(paths, log=print):
    """Run every check after the files are downloaded. Drops a play-by-play file that can't be read
    so the build carries on without it."""
    for name, fn in (("nblr", _check_nblr), ("pbp", _check_pbp), ("feed", _check_feed)):
        try:
            fn(paths)
        except Exception as e:  # noqa: BLE001
            problem(name, f"check failed: {e}")
        if name in _state.get("problems", {}):
            for text in _state["problems"][name]:
                log(f"  data problem ({name}): {text}")
        else:
            ok(name)


def _results(paths):
    results = pd.read_csv(paths["results"], low_memory=False)
    results["match_id"] = results.match_id.astype(str)
    results["utc"] = pd.to_datetime(results.match_time_utc.astype(str).str.replace("T", " ").str[:19],
                                    errors="coerce", utc=True)
    return results


def _check_nblr(paths):
    for key, cols in COLUMNS.items():
        try:
            head = pd.read_csv(paths[key], nrows=5, low_memory=False)
        except Exception as e:  # noqa: BLE001
            problem("nblr", f"{os.path.basename(paths[key])} couldn't be read ({e})")
            continue
        missing = [c for c in cols if c not in head.columns]
        if head.empty:
            problem("nblr", f"{os.path.basename(paths[key])} is empty")
        elif missing:
            problem("nblr", f"{os.path.basename(paths[key])} is missing columns: {', '.join(missing)}")
    if "nblr" in _state.get("problems", {}):
        return

    # Games the fallback had to fetch because nblR still doesn't have them days later.
    extra = paths.get("extra_results")
    if extra and os.path.exists(extra):
        results = _results(paths)
        filled = set(pd.read_csv(extra, dtype={"match_id": str}).match_id)
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=BEHIND_HOURS)
        late = results[results.match_id.isin(filled) & (results.utc < cutoff)]
        if len(late):
            problem("nblr", f"nblR hasn't added {len(late)} game(s) played more than {BEHIND_HOURS // 24} days ago "
                            f"(oldest {late.utc.min():%d %b}). The NBL feed is covering for now.")


def _check_pbp(paths):
    if not paths.get("pbp"):
        return  # the download failure was already recorded
    from .pbp import COLUMNS as needed  # imported here because pbp imports data, which imports this
    try:
        pbp = rds.read_frame(paths["pbp"], needed)
    except Exception as e:  # noqa: BLE001
        problem("pbp", f"pbp.rds couldn't be read ({e}). The site is building without play-by-play stats.")
        del paths["pbp"]
        return
    if pbp.empty:
        problem("pbp", "pbp.rds is empty")
        return
    results = _results(paths)
    latest = results.season.dropna().astype(str).max()
    box = pd.read_csv(paths["box_team"], low_memory=False, usecols=["match_id"])
    have_box = set(box.match_id.astype(str))
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=BEHIND_HOURS)
    played = results[(results.season.astype(str) == latest) & (results.utc < cutoff) & results.match_id.isin(have_box)]
    missing = played[~played.match_id.isin(set(pbp.match_id.astype(str)))]
    if len(missing):
        problem("pbp", f"play-by-play is missing {len(missing)} of {len(played)} games this season that nblR has "
                       f"box scores for (oldest {missing.utc.min():%d %b})")


def _check_feed(paths):
    """Fetch the latest game nblR has from the NBL feed and check the two agree on the score.
    This way a broken feed shows up before the fallback actually needs it."""
    results = _results(paths)
    latest = results.season.dropna().astype(str).max()
    box = pd.read_csv(paths["box_team"], low_memory=False, usecols=["match_id", "home_away", "points", "score"])
    box["match_id"] = box.match_id.astype(str)
    box["pts"] = pd.to_numeric(box.points, errors="coerce").fillna(pd.to_numeric(box.score, errors="coerce"))
    done = results[(results.season.astype(str) == latest)
                   & (results.match_status.astype(str).str.upper() == "COMPLETE")
                   & results.match_id.isin(set(box.match_id))]
    if done.empty:
        return  # nothing played yet this season to compare against
    game = done.sort_values("utc").iloc[-1]
    url = feed.MATCH_URL.format(game.match_id)
    try:
        resp = requests.get(url, headers=feed.HEADERS, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body) if isinstance(body, dict) else body
        if isinstance(data, list):
            data = data[0] if data else {}
        data.setdefault("id", game.match_id)
        _, teams, _ = feed.parse(data, latest)
    except Exception as e:  # noqa: BLE001
        problem("feed", f"couldn't read {game.away_team_name} at {game.home_team_name} ({game.match_id}) "
                        f"from the feed: {e}")
        return
    want = box[box.match_id == game.match_id].drop_duplicates("home_away", keep="last").set_index("home_away").pts
    got = {t["home_away"]: t["points"] for t in teams}
    if any(got.get(side) != want.get(side) for side in ("home", "away")):
        problem("feed", f"feed and nblR disagree on the score of {game.match_id} "
                        f"(feed {got.get('home')}-{got.get('away')}, nblR {want.get('home')}-{want.get('away')})")
