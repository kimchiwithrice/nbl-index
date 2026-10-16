"""Builds the static site: fits the model, then renders one HTML page per team, game and player."""
import json
import math
import os
import re
import shutil

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import data, impact, model, players, sim
from .players import slugify

HERE = os.path.dirname(__file__)
SYDNEY = "Australia/Sydney"


# Template filters

def is_missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


def f1(value):
    return "–" if is_missing(value) else f"{value:.1f}"


def signed(value, decimals=1):
    return "–" if is_missing(value) else f"{value:+.{decimals}f}"


def pct(value, decimals=1):
    return "–" if is_missing(value) else f"{100 * value:.{decimals}f}%"


def chance(value):
    """Format a probability, showing >99% / <1% rather than rounding to 100% or 0%."""
    if value is None:
        return "–"
    if value >= 0.995:
        return ">99%"
    if 0 < value < 0.005:
        return "<1%"
    return f"{100 * value:.0f}%"


def fair_moneyline(p):
    p = min(max(p, 0.01), 0.99)
    return f"{-100 * p / (1 - p):.0f}" if p >= 0.5 else f"+{100 * (1 - p) / p:.0f}"


def sydney_time(ts, fmt="%a %d %b, %I:%M%p"):
    """Format a UTC timestamp in Sydney time, e.g. 'Sat 19 Sep, 7:30pm'."""
    if ts is None or pd.isna(ts):
        return "TBC"
    text = ts.tz_convert(SYDNEY).strftime(fmt).replace("AM", "am").replace("PM", "pm")
    # %-I isn't available on Windows, so drop the leading zero from the hour by hand.
    return re.sub(r"(^|[ ,])0(\d:\d\d)", r"\1\2", text)


POSITIONS = {"G": "Guard", "GRD": "Guard", "PG": "Point guard", "SG": "Shooting guard", "GF": "Guard/forward",
             "F": "Forward", "FWD": "Forward", "SF": "Small forward", "PF": "Power forward",
             "FC": "Forward/centre", "C": "Centre", "CEN": "Centre"}


def position(code):
    return POSITIONS.get(str(code).strip().upper(), str(code))


FILTERS = {"position": position, "f1": f1, "sgn": signed, "pct": pct, "odds": chance, "fair_ml": fair_moneyline,
           "syd": sydney_time}


class Site:
    """Renders templates into the output folder and keeps a list of URLs for the sitemap."""

    def __init__(self, cfg, out_dir, common):
        self.cfg = cfg
        self.out_dir = out_dir
        self.common = common
        self.urls = []
        self.env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                               autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(FILTERS)

    def page(self, path, template, **context):
        """Render `template` to `path` ('teams/perth-wildcats/' becomes teams/perth-wildcats/index.html)."""
        html = self.env.get_template(template).render(cfg=self.cfg, path=path, root=self.cfg.get("base_path", "/"),
                                                      **self.common, **context)
        if path == "" or path.endswith("/"):
            file_path = os.path.join(self.out_dir, path, "index.html")
        else:
            file_path = os.path.join(self.out_dir, path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)
        if template != "404.html":
            self.urls.append(path)


# Spoiler-free game ratings

def drama(margin, overtimes, total, quality):
    """0-100 after the game: how close it finished, overtime, how good the teams were, and scoring.

    Deliberately says nothing about who won, so it's safe to show with scores hidden.
    """
    closeness = min(1, max(0, 1 - (margin - 1) / 24))
    scoring = min(1, max(0, (total - 150) / 60))
    score = 0.62 * closeness + 0.18 * quality + 0.10 * scoring + 0.18 * min(overtimes, 2)
    return round(100 * min(1, score))


def team_quality(net_home, net_away, net_sd):
    """0-1: how good the two teams are together, relative to the league's spread."""
    return min(1, max(0, ((net_home + net_away) / 2) / (2 * net_sd + 1e-9) + 0.5))


def hype(net_home, net_away, p_home, total, net_sd):
    """0-100 before the game: team quality, how close it should be, and expected points."""
    quality = team_quality(net_home, net_away, net_sd)
    closeness = 1 - abs(p_home - 0.5) * 2
    scoring = min(1, max(0, (total - 165) / 40 + 0.5))
    return round(100 * (0.45 * quality + 0.40 * closeness + 0.15 * scoring))


# Build steps

def team_summaries(teams, ratings, preseason, week_ago, results, stats, season_sim, games, sched, cur):
    """Everything shown about each team: ratings, ranks, schedule strength, record and simulation odds."""
    info = {}
    for i, team in enumerate(teams):
        code, slug, short = data.TEAMS.get(team, (team[:3].upper(), slugify(team), team))
        info[team] = {
            "name": team, "code": code, "slug": slug, "short": short,
            "colours": data.TEAM_COLOURS.get(team, data.DEFAULT_COLOURS),
            "o": ratings["mu"] + ratings["off"][i],
            "d": ratings["mu"] + ratings["def"][i],
            "net": ratings["off"][i] - ratings["def"][i],
            "t": ratings["t_mu"] + ratings["tempo"][i],
            "pre": (preseason["off"][i] - preseason["def"][i]) if preseason else None,
            "wk": (week_ago["off"][i] - week_ago["def"][i]) if week_ago else None,
            **{k: results[team][k] for k in ("w", "l", "pf", "pa", "luck", "waa", "gp")},
            "sim": season_sim["teams"][team],
            "stats": stats.get(team),
        }

    net = {t: info[t]["net"] for t in teams}
    this_season = games[games.start == cur]
    for team in teams:
        played = list(this_season[this_season.home == team].away) + list(this_season[this_season.away == team].home)
        info[team]["sos"] = float(np.mean([net[o] for o in played if o in net])) if played else None
        left = sched[~sched.done & ((sched.home == team) | (sched.away == team))]
        to_play = [a if h == team else h for h, a in zip(left.home, left.away)]
        regular_left = left[~left.finals]
        info[team]["left_home"] = int(((regular_left.home == team) & ~regular_left.neutral).sum())
        info[team]["left_away"] = int(((regular_left.away == team) & ~regular_left.neutral).sum())
        info[team]["left_neutral"] = int(regular_left.neutral.sum())
        info[team]["rsos"] = float(np.mean([net[o] for o in to_play if o in net])) if to_play else None

    rank_keys = (("net", True), ("o", True), ("d", False), ("t", True), ("sos", True), ("rsos", True),
                 ("luck", True), ("waa", True), ("pre", True), ("wk", True))
    ranks = {key: model.ranks({t: info[t][key] for t in teams}, higher_is_better)
             for key, higher_is_better in rank_keys}
    stat_ranks = {key: model.ranks({t: (stats[t] or {}).get(key) for t in teams}, higher_is_better)
                  for key, _, higher_is_better, _, _ in model.STAT_DEFS}
    for team in teams:
        info[team]["rk"] = {key: ranks[key].get(team) for key in ranks}
        info[team]["srk"] = {key: stat_ranks[key].get(team) for key in stat_ranks}
        last_week = ranks["wk"].get(team)
        info[team]["move"] = (last_week - ranks["net"][team]) if last_week else 0
    return info


def game_list(sched, games, info, ratings, cal, cur, teams, now):
    """This season's games. Played games get the prediction the model would have made before tip-off."""
    net = {t: info[t]["net"] for t in info}
    net_sd = float(np.std(list(net.values())))
    fits_by_day = {}
    out = []
    for row in sched.itertuples():
        if row.home not in info or row.away not in info:
            continue
        local_day = row.utc.tz_convert(SYDNEY).normalize()
        game = {"id": row.match_id, "utc": row.utc, "home": row.home, "away": row.away,
                "neutral": bool(row.neutral), "round": row.round_number,
                "venue": row.venue_name if isinstance(row.venue_name, str) else "",
                "done": bool(row.done), "finals": bool(row.finals),
                "slug": f"{local_day:%Y-%m-%d}-{info[row.away]['slug']}-at-{info[row.home]['slug']}"}
        if game["done"]:
            if local_day not in fits_by_day:
                fits_by_day[local_day] = model.fit(games, local_day.tz_convert("UTC"), cur, teams)
            pregame_ratings = fits_by_day[local_day] or ratings
            game["pred"] = model.predict(pregame_ratings, cal, row.home, row.away, row.neutral)
            game["hs"], game["as"], game["ot"] = int(row.h_pts), int(row.a_pts), int(row.ot or 0)
            pre_net = {t: pregame_ratings["off"][pregame_ratings["idx"][t]] - pregame_ratings["def"][pregame_ratings["idx"][t]]
                       for t in (row.home, row.away) if t in pregame_ratings["idx"]}
            h_net, a_net = pre_net.get(row.home, net[row.home]), pre_net.get(row.away, net[row.away])
            game["drama"] = drama(abs(game["hs"] - game["as"]), game["ot"], game["hs"] + game["as"],
                                  team_quality(h_net, a_net, net_sd))
            game["correct"] = (game["pred"]["p_home"] >= 0.5) == (game["hs"] > game["as"])
        else:
            game["pred"] = model.predict(ratings, cal, row.home, row.away, row.neutral)
            game["hype"] = hype(net[row.home], net[row.away], game["pred"]["p_home"],
                                game["pred"]["total"], net_sd)
        # Started, but the result isn't in the data yet.
        game["pending"] = not game["done"] and game["utc"] < now + pd.Timedelta(minutes=10)
        out.append(game)
    return out, net_sd


def season_record(completed):
    """Accuracy of this season's pre-game predictions."""
    if not completed:
        return None
    home_won = np.array([g["hs"] > g["as"] for g in completed], float)
    p = np.clip(np.array([g["pred"]["p_home"] for g in completed]), 1e-6, 1 - 1e-6)
    return {"n": len(completed),
            "acc": float(np.mean((p >= 0.5) == (home_won == 1))),
            "logloss": float(-np.mean(home_won * np.log(p) + (1 - home_won) * np.log(1 - p))),
            "mae": float(np.mean([abs((g["hs"] - g["as"]) - g["pred"]["margin"]) for g in completed]))}


# Percentile bars on player pages: (label, column, higher is better, format)
PERCENTILE_STATS = [("Impact", "impact", True, "sgn"), ("Points per 40", "p40", True, "f1"),
                    ("True shooting", "ts", True, "pct"), ("Usage", "usg", True, "pct"),
                    ("Assist %", "ast_pct", True, "pct"), ("Rebound %", "reb_pct", True, "pct"),
                    ("Steal %", "stl_pct", True, "pct"), ("Block %", "blk_pct", True, "pct"),
                    ("Turnover %", "tov_pct", False, "pct")]


def player_pages(seasons, cur, pb, info):
    pages = {}
    current = seasons[seasons.start == cur].copy()
    current["reb_pct"] = (current.orb_pct + current.drb_pct) / 2
    pool = current[(current.mpg >= 10) & (current.gp >= 3)]
    for slug, rows in seasons.groupby("slug"):
        rows = rows.sort_values(["start", "min"])
        latest = rows.iloc[-1]
        page = {"name": latest.player, "slug": slug, "team": latest.team, "pos": latest.pos,
                "rows": rows.to_dict("records"), "current": bool((rows.start == cur).any())}
        if page["current"]:
            me = current[current.slug == slug].sort_values("min").iloc[-1]
            page["impact_rank"] = int((pool.impact > me.impact).sum() + 1) if me.impact == me.impact else None
            page["pool_n"] = len(pool)
            page["percentiles"] = []
            for label, col, higher, fmt in PERCENTILE_STATS:
                value, others = me[col], pool[col].dropna()
                if value != value or others.empty:
                    continue
                share = (others < value).mean() if higher else (others > value).mean()
                page["percentiles"].append({"label": label, "value": value, "fmt": fmt, "pct": round(100 * share)})
            page["log_svg"] = game_log_svg(pb[(pb.player == latest.player) & (pb.start == cur)], latest.team, info)
        pages[slug] = page
    return pages


def game_log_svg(log, team, info, width=680, height=200):
    """Points in every game this season, with minutes as a faint bar behind."""
    log = log.sort_values("utc")
    if log.empty:
        return ""
    n = len(log)
    left, right, top, bottom = 30, 8, 18, 34
    top_pts = max(10, math.ceil(log.pts.max() / 10) * 10)
    slot = (width - left - right) / max(n, 8)
    bar = min(26, slot * 0.7)
    colour = info[team]["colours"] if team in info else data.DEFAULT_COLOURS

    def y(v, top_v):
        return top + (height - top - bottom) * (1 - v / top_v)

    svg = [f'<svg viewBox="0 0 {width} {height}" class="chart glog" role="img" '
           f'aria-label="Points in each game this season" style="--c:{colour[0]};--cd:{colour[1]}"><g>']
    for v in range(0, top_pts + 1, 10):
        svg.append(f'<line x1="{left}" x2="{width - right}" y1="{y(v, top_pts):.1f}" y2="{y(v, top_pts):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y(v, top_pts) + 4:.1f}" class="ax" text-anchor="end">{v}</text>')
    for i, r in enumerate(log.itertuples()):
        x = left + slot * i + (slot - bar) / 2
        opp = r.away if r.team == r.home else r.home
        code = info[opp]["code"] if opp in info else str(opp)[:3].upper()
        where = "v" if r.team == r.home else "@"
        mins = y(min(r.min, 40) / 40 * top_pts, top_pts)
        svg.append(f'<rect x="{x - 2:.1f}" y="{mins:.1f}" width="{bar + 4:.1f}" height="{height - bottom - mins:.1f}" class="min"/>')
        svg.append(f'<rect x="{x:.1f}" y="{y(r.pts, top_pts):.1f}" width="{bar:.1f}" '
                   f'height="{height - bottom - y(r.pts, top_pts):.1f}" class="pts"><title>{where} {code}: {int(r.pts)} pts in {r.min:.0f} min</title></rect>')
        svg.append(f'<text x="{x + bar / 2:.1f}" y="{y(r.pts, top_pts) - 4:.1f}" class="val" text-anchor="middle">{int(r.pts)}</text>')
        svg.append(f'<text x="{x + bar / 2:.1f}" y="{height - bottom + 14:.1f}" class="ax" text-anchor="middle">{code}</text>')
    svg.append("</g></svg>")
    return "".join(svg)


def build(cfg, data_dir, out_dir, refresh=False, n_sims=sim.N_SIMS, log=print, ledger_path=None):
    paths = data.fetch(data_dir, refresh, log)
    games, sched = data.load(paths)
    cur = int(sched.start.max())
    season = f"{cur}-{str(cur + 1)[-2:]}"
    teams = sorted(set(sched.home) | set(sched.away))
    now = pd.Timestamp.now(tz="UTC")
    log(f"{len(games)} games loaded; {season} season: {int(sched.done.sum())} played, "
        f"{int((~sched.done).sum())} to go")

    log("Backtesting ...")
    backtest, cal = model.backtest(games)
    ratings = model.fit(games, now, cur, teams)
    preseason = model.fit(games, sched.utc.min() - pd.Timedelta(hours=1), cur, teams)
    week_ago = model.fit(games, now - pd.Timedelta(days=7), cur, teams)
    stats = model.team_stats(games, cur, teams)
    results = model.results_table(games, cur, ratings, cal, teams)
    log("Rating history ...")
    history = model.history(games, cur, teams)
    log(f"Simulating the season {n_sims:,} times ...")
    season_sim = sim.simulate(ratings, cal, sched, teams, n=n_sims)

    info = team_summaries(teams, ratings, preseason, week_ago, results, stats, season_sim, games, sched, cur)
    order = sorted(teams, key=lambda t: info[t]["rk"]["net"])

    all_games, net_sd = game_list(sched, games, info, ratings, cal, cur, teams, now)

    log("Player impact and availability ...")
    player_box = data.load_players(paths)
    pb = impact.prepare(player_box, games)
    weights = impact.fit_weights(pb)
    values_now = impact.current_values(pb, weights, cur)
    absences = {t: impact.missing_regulars(pb, t, cur, values_now) for t in teams}
    adjust_for_absences(all_games, absences, cal)

    upcoming = [g for g in all_games if not g["done"] and not g["pending"]]
    pending = [g for g in all_games if g["pending"]]
    completed = [g for g in all_games if g["done"]]
    ledger = update_ledger(ledger_path, upcoming, completed, now) if ledger_path else None

    log("Player stats ...")
    seasons = players.assign_slugs(players.player_seasons(player_box, games))
    by_season = impact.season_values(pb, weights)
    averages = {s: impact.league_average(by_season, pb, s) for s in seasons.start.unique()}
    keys = list(zip(seasons.player, seasons.team, seasons.start))
    seasons["impact"] = [by_season.get(k, np.nan) - averages.get(k[2], 0.0) for k in keys]
    current = seasons[seasons.start == cur]
    rosters = {t: current[current.team == t].sort_values("min", ascending=False).to_dict("records") for t in teams}

    regular = sched[~sched.finals]
    n_games = int(max(((regular.home == t) | (regular.away == t)).sum() for t in teams))
    for team in teams:
        wins = info[team]["sim"]["wins"]
        info[team]["proj_rec"] = f"{round(wins)}-{n_games - round(wins)}"
        info[team]["likely"] = int(np.argmax(info[team]["sim"]["pos_dist"])) + 1
    standings = sorted(teams, key=lambda t: (-info[t]["w"], info[t]["l"], -(info[t]["pf"] - info[t]["pa"])))
    projected = sorted(teams, key=lambda t: (-info[t]["sim"]["wins"], info[t]["sim"]["pos"]))
    missing_stats = [k for k, *_ in model.STAT_DEFS if all((stats[t] or {}).get(k) is None for t in teams)]

    # Write the site
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(out_dir, "assets"))
    write_data_js(out_dir, ratings, cal, info, teams, season, bool(cfg.get("show_betting_tools")))
    common = {"season": season, "updated": sydney_time(now, "%d %b %Y, %I:%M%p %Z"),
              "info": info, "order": order, "teams": teams, "rating": cfg.get("rating_name", "Rating"),
              "N": len(teams)}
    site = Site(cfg, out_dir, common)

    em_max = max(abs(info[t]["net"]) for t in teams) or 1
    site.page("", "index.html", upcoming=upcoming[:6], em_max=em_max, S=season_sim, title=None, desc=None)
    site.page("schedule/", "schedule.html", upcoming=upcoming, completed=completed[::-1], pending=pending,
              net_sd=net_sd)
    site.page("ladder/", "ladder.html", S=season_sim, standings=standings, projected=projected, n_games=n_games)
    site.page("stats/", "stats.html", defs=model.STAT_DEFS, missing=missing_stats)
    site.page("players/", "players.html",
              players=sorted(current.to_dict("records"), key=lambda r: -r["min"]))
    site.page("matchup/", "matchup.html")
    site.page("model/", "model.html", bt=backtest, cal=cal, R=ratings, replay=season_record(completed),
              ledger=ledger)
    site.page("about/", "about.html")
    site.page("glossary/", "glossary.html")
    for team in teams:
        site.page(f"teams/{info[team]['slug']}/", "team.html", t=team, x=info[team],
                  games=[g for g in all_games if team in (g["home"], g["away"])], roster=rosters[team],
                  chart=history_svg(history, team, teams), defs=model.STAT_DEFS, S=season_sim)
    for game in all_games:
        site.page(f"games/{game['slug']}/", "game.html", g=game, h=info[game["home"]], a=info[game["away"]],
                  defs=model.STAT_DEFS, ld=event_ld(game, info[game["home"]], info[game["away"]], cfg, cfg.get("base_path", "/")),
                  impact_cap=impact.MAX_TEAM_PTS, S=season_sim)
    for page in player_pages(seasons, cur, pb, info).values():
        site.page(f"players/{page['slug']}/", "player.html", p=page)
    site.page("404.html", "404.html")
    write_sitemap(site, cfg, now)
    log(f"Wrote {len(site.urls)} pages to {out_dir}")
    return {"pages": len(site.urls), "backtest": backtest, "ratings": ratings}


# Output files

def write_data_js(out_dir, ratings, cal, info, teams, season, betting=False):
    """Ratings for the matchup page's calculator."""
    payload = {
        "season": season, "betting": betting, "mu": ratings["mu"], "hfa": ratings["hfa"],
        "t_mu": ratings["t_mu"], "cal": cal,
        "teams": {t: {"name": t, "code": info[t]["code"], "slug": info[t]["slug"],
                      "off": float(ratings["off"][i]), "def": float(ratings["def"][i]),
                      "tempo": float(ratings["tempo"][i]), "o": info[t]["o"], "d": info[t]["d"],
                      "net": info[t]["net"], "t": info[t]["t"], "rk": info[t]["rk"]}
                  for i, t in enumerate(teams)},
    }
    with open(os.path.join(out_dir, "assets", "data.js"), "w", encoding="utf-8") as f:
        f.write("window.NBL=" + json.dumps(payload, separators=(",", ":"), default=float) + ";")


def adjust_for_absences(all_games, absences, cal):
    """Move each team's next prediction for regulars who missed its last game."""
    next_game = {}
    for g in all_games:
        if not g["done"] and not g["pending"]:
            for t in (g["home"], g["away"]):
                next_game.setdefault(t, g["id"])
    for g in all_games:
        out = {side: (absences.get(g[side], []) if next_game.get(g[side]) == g["id"] else [])
               for side in ("home", "away")}
        if not out["home"] and not out["away"]:
            continue
        p = g["pred"]
        to_pts = p["poss"] / 100 * cal["k"]
        raw = {side: sum(a["per100"] for a in out[side]) * to_pts for side in out}
        lost = {side: min(raw[side], impact.MAX_TEAM_PTS) for side in out}
        shift = lost["away"] - lost["home"]
        p["margin"] += shift
        p["h_pts"], p["a_pts"] = p["total"] / 2 + p["margin"] / 2, p["total"] / 2 - p["margin"] / 2
        p["p_home"] = model.normal_cdf(p["margin"] / cal["sigma"])
        for side in out:
            for a in out[side]:
                a["pts"] = a["per100"] * to_pts * (lost[side] / raw[side] if raw[side] else 0)
        g["adjust"] = {"home": out["home"], "away": out["away"], "shift": shift,
                       "capped": any(raw[s] > impact.MAX_TEAM_PTS for s in out)}


def event_ld(game, home, away, cfg, root):
    """schema.org SportsEvent for a game page, with the fields Google asks for."""
    city, region, country = data.VENUE_PLACES.get(game["venue"]) or data.HOME_PLACES.get(game["home"], ("", "", "AU"))
    address = {"@type": "PostalAddress", "addressLocality": city, "addressCountry": country}
    if region:
        address["addressRegion"] = region
    base = cfg["base_url"].rstrip("/") + root
    return {
        "@context": "https://schema.org", "@type": "SportsEvent",
        "name": f"{away['name']} vs {home['name']}",
        "startDate": game["utc"].isoformat(),
        "endDate": (game["utc"] + pd.Timedelta(hours=2, minutes=15)).isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "sport": "Basketball",
        "image": [base + "assets/share.png"],
        "description": f"{away['name']} at {home['name']}, NBL. Prediction and matchup stats.",
        "location": {"@type": "Place", "name": game["venue"] or home["name"], "address": address},
        "organizer": {"@type": "SportsOrganization", "name": "National Basketball League", "url": "https://nbl.com.au"},
        "homeTeam": {"@type": "SportsTeam", "name": home["name"]},
        "awayTeam": {"@type": "SportsTeam", "name": away["name"]},
        "competitor": [{"@type": "SportsTeam", "name": home["name"]}, {"@type": "SportsTeam", "name": away["name"]}],
    }


def history_svg(history, team, teams, width=680, height=250):
    """Line chart of every team's rating through the season.

    The page's team is drawn bold in its club colour; everyone else is a faint line in their own
    colour with a short label at the end, so you can still tell who's who.
    """
    if len(history) < 2:
        return ""
    left, right, top, bottom = 34, 44, 10, 26
    values = [point["net"][t] for point in history for t in teams]
    lo = math.floor(min(values + [-1]) / 5) * 5
    hi = math.ceil(max(values + [1]) / 5) * 5
    n = len(history)

    def x(i):
        return left + (width - left - right) * i / (n - 1)

    def y(v):
        return top + (height - top - bottom) * (hi - v) / (hi - lo)

    def colours(t):
        light, dark = data.TEAM_COLOURS.get(t, data.DEFAULT_COLOURS)
        return f"--c:{light};--cd:{dark}"

    def short(t):
        return data.TEAMS.get(t, (t[:3].upper(),))[0]

    # End labels: start at each line's last value, then push apart so they don't overlap.
    order = sorted(teams, key=lambda t: -history[-1]["net"][t])
    label_y, last = {}, None
    for t in order:
        ly = y(history[-1]["net"][t]) + 4
        if last is not None and ly < last + 11:
            ly = last + 11
        label_y[t] = last = ly
    overflow = last - (height - bottom + 4)
    if overflow > 0:
        for t in order:
            label_y[t] -= overflow * (order.index(t) + 1) / len(order)

    svg = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="AdjEM through the season, every team">']
    for v in range(int(lo), int(hi) + 1, 5):
        svg.append(f'<line x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                   f'class="{"zero" if v == 0 else "grid"}"/>'
                   f'<text x="{left - 6}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{v:+d}</text>')
    for i in range(0, n, max(1, n // 6)):
        svg.append(f'<text x="{x(i):.1f}" y="{height - 6}" class="ax" text-anchor="middle">{history[i]["label"]}</text>')
    for t in [t for t in teams if t != team] + [team]:
        points = " ".join(f"{x(i):.1f},{y(p['net'][t]):.1f}" for i, p in enumerate(history))
        cls = "me" if t == team else "other"
        svg.append(f'<g class="{cls}" style="{colours(t)}"><title>{t}: {history[-1]["net"][t]:+.1f}</title>'
                   f'<polyline points="{points}"/>'
                   f'<text x="{width - right + 8}" y="{label_y[t]:.1f}" class="lab">{short(t)}</text>')
        if t == team:
            svg.append(f'<circle cx="{x(n - 1):.1f}" cy="{y(history[-1]["net"][t]):.1f}" r="4"/>')
        svg.append("</g>")
    svg.append("</svg>")
    return "".join(svg)


def update_ledger(path, upcoming, completed, now):
    """Log each prediction before tip-off, then add the result once the game is played."""
    ledger = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            ledger = json.load(f)
    for game in upcoming:
        if game["utc"] > now:  # the last version made before tip-off is the one that counts
            p = game["pred"]
            ledger[game["id"]] = {"utc": game["utc"].isoformat(), "home": game["home"], "away": game["away"],
                                  "slug": game["slug"], "p_home": round(p["p_home"], 4),
                                  "margin": round(p["margin"], 2), "total": round(p["total"], 1),
                                  "logged": now.isoformat()}
            if game.get("adjust"):  # how far the injury adjustment moved the margin, to check it later
                ledger[game["id"]]["adj"] = round(game["adjust"]["shift"], 2)
    for game in completed:
        entry = ledger.get(game["id"])
        if entry and "hs" not in entry:
            entry["hs"], entry["as"] = game["hs"], game["as"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)

    pending = sum(1 for e in ledger.values() if "hs" not in e)
    graded = [e for e in ledger.values() if "hs" in e]
    if not graded:
        return {"n": 0, "pending": pending, "rows": []}
    home_won = np.array([e["hs"] > e["as"] for e in graded], float)
    p = np.clip(np.array([e["p_home"] for e in graded]), 1e-6, 1 - 1e-6)
    return {"n": len(graded), "pending": pending,
            "acc": float(np.mean((p >= 0.5) == (home_won == 1))),
            "logloss": float(-np.mean(home_won * np.log(p) + (1 - home_won) * np.log(1 - p))),
            "mae": float(np.mean([abs((e["hs"] - e["as"]) - e["margin"]) for e in graded])),
            "since": min(e["logged"] for e in ledger.values())[:10],
            "rows": sorted(graded, key=lambda e: e["utc"], reverse=True)[:30]}


def write_sitemap(site, cfg, now):
    base = cfg.get("base_url", "").rstrip("/")
    today = now.strftime("%Y-%m-%d")
    with open(os.path.join(site.out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in site.urls:
            f.write(f"  <url><loc>{base}/{url}</loc><lastmod>{today}</lastmod></url>\n")
        f.write("</urlset>\n")
    with open(os.path.join(site.out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
