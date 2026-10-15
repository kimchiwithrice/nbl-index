"""Downloads and cleans the NBL data.

The data comes from the nblR project's releases (github.com/JaseZiv/nblr_data), which are
collected from the NBL's official stats feed. Finished games nblR hasn't caught up on yet are
fetched straight from the NBL feed by feed.py.
"""
import os
import time

import numpy as np
import pandas as pd
import requests

from . import feed

RELEASE_URL = "https://github.com/JaseZiv/nblr_data/releases/download/{}"
FILES = {
    "results": "match_results/results_wide.csv",
    "box_team": "box_team/box_team.csv",
    "box_player": "box_player/box_player.csv",
}
MAX_AGE_HOURS = 3  # reuse downloaded files younger than this

# The feed uses a couple of different names for the same club.
NAME_FIXES = {"NZ Breakers": "New Zealand Breakers", "The Hawks": "Illawarra Hawks"}

# Full name: (code, URL slug, short name)
TEAMS = {
    "Adelaide 36ers": ("ADL", "adelaide-36ers", "Adelaide"),
    "Brisbane Bullets": ("BRI", "brisbane-bullets", "Brisbane"),
    "Cairns Taipans": ("CNS", "cairns-taipans", "Cairns"),
    "Illawarra Hawks": ("ILL", "illawarra-hawks", "Illawarra"),
    "Melbourne United": ("MEL", "melbourne-united", "Melbourne"),
    "New Zealand Breakers": ("NZB", "new-zealand-breakers", "NZ Breakers"),
    "Perth Wildcats": ("PER", "perth-wildcats", "Perth"),
    "South East Melbourne Phoenix": ("SEM", "south-east-melbourne-phoenix", "SE Melbourne"),
    "Sydney Kings": ("SYD", "sydney-kings", "Sydney"),
    "Tasmania JackJumpers": ("TAS", "tasmania-jackjumpers", "Tasmania"),
    "Townsville Crocodiles": ("TSV", "townsville-crocodiles", "Townsville"),
}

# Where games are played: venue -> (city, region, country code). Used for the event details
# search engines read on game pages. Unknown venues fall back to the home club's city.
VENUE_PLACES = {
    "John Cain Arena": ("Melbourne", "VIC", "AU"), "Melbourne Arena": ("Melbourne", "VIC", "AU"),
    "State Basketball Centre": ("Melbourne", "VIC", "AU"), "RAC Arena": ("Perth", "WA", "AU"),
    "WIN Entertainment Centre": ("Wollongong", "NSW", "AU"), "Qudos Bank Arena": ("Sydney", "NSW", "AU"),
    "Cairns Convention Centre": ("Cairns", "QLD", "AU"), "Adelaide Entertainment Centre": ("Adelaide", "SA", "AU"),
    "Titanium Security Arena": ("Adelaide", "SA", "AU"), "MyState Bank Arena": ("Hobart", "TAS", "AU"),
    "Silverdome": ("Launceston", "TAS", "AU"), "Spark Arena": ("Auckland", "", "NZ"),
    "Nissan Arena": ("Brisbane", "QLD", "AU"), "Brisbane Entertainment Centre": ("Brisbane", "QLD", "AU"),
    "Wolfbrook Arena": ("Christchurch", "", "NZ"), "AIS Arena": ("Canberra", "ACT", "AU"),
    "Gold Coast Convention Centre": ("Gold Coast", "QLD", "AU"), "Townsville Entertainment Centre": ("Townsville", "QLD", "AU"),
}
HOME_PLACES = {
    "Adelaide 36ers": ("Adelaide", "SA", "AU"), "Brisbane Bullets": ("Brisbane", "QLD", "AU"),
    "Cairns Taipans": ("Cairns", "QLD", "AU"), "Illawarra Hawks": ("Wollongong", "NSW", "AU"),
    "Melbourne United": ("Melbourne", "VIC", "AU"), "New Zealand Breakers": ("Auckland", "", "NZ"),
    "Perth Wildcats": ("Perth", "WA", "AU"), "South East Melbourne Phoenix": ("Melbourne", "VIC", "AU"),
    "Sydney Kings": ("Sydney", "NSW", "AU"), "Tasmania JackJumpers": ("Hobart", "TAS", "AU"),
}

# Club colours for charts: (light background, dark background). Taken from each club's palette,
# with a lighter shade for dark mode where the main colour is too dark to see.
TEAM_COLOURS = {
    "Adelaide 36ers": ("#16307a", "#7f97e8"),
    "Brisbane Bullets": ("#064590", "#5d9be6"),
    "Cairns Taipans": ("#e0701b", "#f79239"),
    "Illawarra Hawks": ("#c81e25", "#f2554e"),
    "Melbourne United": ("#0054ff", "#5a90ff"),
    "New Zealand Breakers": ("#111111", "#e6e6e6"),
    "Perth Wildcats": ("#d40000", "#ff5a5a"),
    "South East Melbourne Phoenix": ("#4e9a1b", "#69be29"),
    "Sydney Kings": ("#5b4099", "#a88ce8"),
    "Tasmania JackJumpers": ("#008c46", "#37b44b"),
}
DEFAULT_COLOURS = ("#555555", "#bbbbbb")

# Short column names used in the games table -> column names in the box score file.
BOX_COLUMNS = {
    "pts": "points", "fgm": "field_goals_made", "fga": "field_goals_attempted",
    "fg3m": "three_pointers_made", "fg3a": "three_pointers_attempted",
    "fg2m": "two_pointers_made", "fg2a": "two_pointers_attempted",
    "ftm": "free_throws_made", "fta": "free_throws_attempted",
    "orb": "rebounds_offensive", "drb": "rebounds_defensive", "ast": "assists",
    "tov": "turnovers", "stl": "steals", "blk": "blocks", "pf": "fouls_personal",
    "paint": "points_in_the_paint", "fbp": "points_fast_break", "scp": "points_second_chance",
    "pot": "points_from_turnovers", "bench": "bench_points",
    "lead": "biggest_lead", "lchg": "lead_changes",
}


def clean_name(name):
    name = str(name).strip()
    return NAME_FIXES.get(name, name)


def season_start(season):
    """'2026-2027' -> 2026"""
    return int(str(season)[:4])


def download(url, path, log, attempts=4):
    for attempt in range(attempts):
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                os.replace(tmp, path)
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            log("  retrying ...")
            time.sleep(10 * (attempt + 1))


def fetch(data_dir, refresh=False, log=print, fallback=True):
    """Download the data files unless recent copies exist. Returns {name: local path}."""
    os.makedirs(data_dir, exist_ok=True)
    paths = {}
    for key, release_path in FILES.items():
        path = os.path.join(data_dir, os.path.basename(release_path))
        paths[key] = path
        is_recent = os.path.exists(path) and (time.time() - os.path.getmtime(path)) / 3600 < MAX_AGE_HOURS
        if is_recent and not refresh:
            continue
        log(f"Downloading {os.path.basename(release_path)} ...")
        try:
            download(RELEASE_URL.format(release_path), path, log)
        except Exception as e:
            if not os.path.exists(path):
                raise
            log(f"  download failed ({e}); using the previous copy")
    if fallback:
        added = feed.fill_missing(data_dir, paths, log)
        if added:
            log(f"  added {added} game(s) from the NBL feed")
    for key, name in feed.EXTRA_FILES.items():
        extra = os.path.join(data_dir, name)
        if os.path.exists(extra):
            paths["extra_" + key] = extra
    return paths


def _with_extra(df, paths, key):
    """Append rows the fallback fetched for games nblR doesn't have yet."""
    extra = paths.get("extra_" + key)
    if not extra or not os.path.exists(extra):
        return df
    rows = pd.read_csv(extra, low_memory=False)
    rows["match_id"] = rows.match_id.astype(str)
    df["match_id"] = df.match_id.astype(str)
    rows = rows[~rows.match_id.isin(set(df.match_id))]
    return pd.concat([df, rows], ignore_index=True)


def load(paths):
    """Returns (games, schedule).

    games:    one row per completed game, with both teams' box totals (h_* and a_* columns)
    schedule: every game of the latest season, played or not
    """
    results = pd.read_csv(paths["results"], low_memory=False)
    if paths.get("extra_results") and os.path.exists(paths["extra_results"]):
        done = pd.read_csv(paths["extra_results"], dtype={"match_id": str}).set_index("match_id")
        results["match_id"] = results.match_id.astype(str)
        hit = results.match_id.isin(done.index)
        for col in done.columns:
            if col in results.columns:
                results[col] = results[col].astype(object)
            results.loc[hit, col] = results.loc[hit, "match_id"].map(done[col])
    results["home"] = results.home_team_name.map(clean_name)
    results["away"] = results.away_team_name.map(clean_name)
    results["utc"] = pd.to_datetime(results.match_time_utc.astype(str).str.replace("T", " ").str[:19],
                                    errors="coerce", utc=True, format="%Y-%m-%d %H:%M:%S")
    results["neutral"] = results.at_neutral_venue.fillna(0).astype(float) > 0
    results["ot"] = results.extra_periods_used.fillna(0).astype(int)
    results["finals"] = results.match_type.astype(str).str.upper().eq("FINALS")
    results["start"] = results.season.map(season_start)

    box = _with_extra(pd.read_csv(paths["box_team"], low_memory=False), paths, "box_team")
    box["team"] = box.name.map(clean_name)
    box = box[["match_id", "home_away", "team", "score"] + list(BOX_COLUMNS.values())].copy()
    for col in BOX_COLUMNS.values():
        box[col] = pd.to_numeric(box[col], errors="coerce")
    # Recent seasons leave some team totals blank: points come from "score", and bench points
    # are rebuilt from the player box scores.
    box["points"] = box.points.fillna(pd.to_numeric(box.score, errors="coerce"))
    if os.path.exists(paths.get("box_player", "")):
        players = _with_extra(pd.read_csv(paths["box_player"], low_memory=False,
                                          usecols=["match_id", "team_name", "starter", "points"]), paths, "box_player")
        players = players[["match_id", "team_name", "starter", "points"]]
        players["team"] = players.team_name.map(clean_name)
        players["points"] = pd.to_numeric(players.points, errors="coerce").fillna(0)
        bench = (players[pd.to_numeric(players.starter, errors="coerce").fillna(1) == 0]
                 .groupby(["match_id", "team"]).points.sum().rename("bench_from_players").reset_index())
        bench["match_id"] = bench.match_id.astype(str)
        box["match_id"] = box.match_id.astype(str)
        box = box.merge(bench, on=["match_id", "team"], how="left")
        box["bench_points"] = box.bench_points.fillna(box.bench_from_players)
        box = box.drop(columns="bench_from_players")
    box = box.drop(columns=["team", "score"])

    home = box[box.home_away == "home"].drop(columns="home_away").rename(
        columns={v: "h_" + k for k, v in BOX_COLUMNS.items()})
    away = box[box.home_away == "away"].drop(columns="home_away").rename(
        columns={v: "a_" + k for k, v in BOX_COLUMNS.items()})
    # The files occasionally contain the same game twice.
    home = home.drop_duplicates("match_id", keep="last")
    away = away.drop_duplicates("match_id", keep="last")
    both = home.merge(away, on="match_id")
    both["match_id"] = both.match_id.astype(str)
    results["match_id"] = results.match_id.astype(str)
    results = results.drop_duplicates("match_id", keep="last")

    completed = results[results.match_status.astype(str).str.upper() == "COMPLETE"]
    games = completed.merge(both, on="match_id", how="inner")
    games = games[(games.h_fga > 20) & (games.a_fga > 20)].copy()
    games["minutes"] = 40 + 5 * games.ot
    # Possessions estimated from the box score, averaged over both teams.
    for side in ("h", "a"):
        games[f"{side}_poss"] = (games[f"{side}_fga"] - games[f"{side}_orb"] + games[f"{side}_tov"]
                                 + 0.44 * games[f"{side}_fta"])
    games["poss"] = (games.h_poss + games.a_poss) / 2
    games = games.sort_values("utc").reset_index(drop=True)

    latest = results.start.max()
    schedule = results[results.start == latest].copy().sort_values("utc").reset_index(drop=True)
    schedule["done"] = schedule.match_id.isin(set(games.match_id))
    schedule = schedule.merge(games[["match_id", "h_pts", "a_pts"]], on="match_id", how="left")
    cols = ["match_id", "season", "start", "utc", "home", "away", "neutral", "finals", "round_number",
            "venue_name", "done", "h_pts", "a_pts", "ot"]
    return games, schedule[cols]


def load_players(paths):
    """Player box scores, one row per player per game they played in."""
    players = _with_extra(pd.read_csv(paths["box_player"], low_memory=False), paths, "box_player")
    players["team"] = players.team_name.map(clean_name)
    players["opp"] = players.opp_name.map(clean_name)
    players["match_id"] = players.match_id.astype(str)
    players["start"] = players.season.map(season_start)

    # Minutes are in "seconds" in recent seasons and "MM:SS" in older ones.
    seconds = pd.to_numeric(players.seconds, errors="coerce")
    mm_ss = players.minutes.astype(str).str.split(":", expand=True)
    from_mm_ss = (pd.to_numeric(mm_ss[0], errors="coerce")
                  + pd.to_numeric(mm_ss.get(1), errors="coerce").fillna(0) / 60)
    players["min"] = np.where(seconds.notna() & (seconds > 0), seconds / 60, from_mm_ss).astype(float)
    players["min"] = players["min"].fillna(0)
    players["player"] = (players.first_name.fillna("").astype(str).str.strip() + " "
                         + players.family_name.fillna("").astype(str).str.strip()).str.strip()

    # Plus-minus moved to a different column in recent seasons.
    pm = pd.to_numeric(players.plus_minus_points, errors="coerce")
    players["plus_minus_points"] = pm.where(pm.notna() & (pm != 0),
                                            pd.to_numeric(players.plus_minus, errors="coerce"))
    for col in ("points", "field_goals_made", "field_goals_attempted", "three_pointers_made",
                "three_pointers_attempted", "free_throws_made", "free_throws_attempted",
                "rebounds_offensive", "rebounds_defensive", "assists", "turnovers", "steals", "blocks",
                "fouls_personal", "plus_minus_points"):
        players[col] = pd.to_numeric(players[col], errors="coerce").fillna(0)
    return players[players["min"] > 0].copy()
