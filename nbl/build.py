"""Builds the static site: fits the model, then renders one HTML page per team, game and player."""
import json
import math
import os
import hashlib
import html as htmllib
import re
import shutil

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import archive, data, feed, impact, league, model, pbp, players, sim
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


def suffix(n):
    """Ordinal ending for a whole number: 1 -> 'st', 12 -> 'th', 22 -> 'nd'."""
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


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


def surname(name):
    """'Jo Lual-Acuil Jr' -> 'Lual-Acuil Jr', 'Bryce Cotton' -> 'Cotton'. For tight lineup tables."""
    parts = str(name).split()
    if len(parts) > 2 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv"):
        return " ".join(parts[-2:])
    return parts[-1] if parts else ""


FILTERS = {"surname": surname, "position": position, "f1": f1, "sgn": signed, "pct": pct, "odds": chance, "fair_ml": fair_moneyline,
           "syd": sydney_time, "suffix": suffix}


class Site:
    """Renders templates into the output folder and keeps a list of URLs for the sitemap."""

    def __init__(self, cfg, out_dir, common):
        self.cfg = cfg
        self.out_dir = out_dir
        self.common = common
        self.urls = []
        self.hashes = {}
        self.env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                               autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
        self.env.filters.update(FILTERS)

    def page(self, path, template, listed=True, **context):
        """Render `template` to `path` ('teams/perth-wildcats/' becomes teams/perth-wildcats/index.html).
        Pages with listed=False are still written but left out of the sitemap."""
        html = self.env.get_template(template).render(cfg=self.cfg, path=path, root=self.cfg.get("base_path", "/"),
                                                      **self.common, **context)
        html = add_jump_links(html)
        if path == "" or path.endswith("/"):
            file_path = os.path.join(self.out_dir, path, "index.html")
        else:
            file_path = os.path.join(self.out_dir, path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)
        if template != "404.html" and listed:
            self.urls.append(path)
            self.hashes[path] = page_hash(html)


# Bits that change on every build even when nothing on the page has
VOLATILE = re.compile(r"Updated [^<.]*(?:\(Sydney time\))?|\?v=[0-9a-f]+")


def page_hash(html):
    return hashlib.md5(VOLATILE.sub("", html).encode("utf-8")).hexdigest()[:12]


H2 = re.compile(r"<h2>(.*?)</h2>", re.S)
INTRO = re.compile(r"</h1>\s*<p class=\"(?:sub|lede)\">.*?</p>(?:\s*<p class=\"stamp\">.*?</p>)?(?:\s*<nav class=\"tabs\".*?</nav>)?", re.S)


def add_jump_links(html, min_sections=3):
    """On longer pages, list the section headings under the intro so it's clear what's further down."""
    heads = H2.findall(html)
    if len(heads) < min_sections:
        return html
    links, used = [], set()
    def anchor(m):
        label = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        slug = base = slugify(htmllib.unescape(label)) or "section"
        n = 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        links.append(f'<a href="#{slug}">{label}</a>')
        return f'<h2 id="{slug}">{m.group(1)}</h2>'
    html = H2.sub(anchor, html)
    nav = '<nav class="jump" aria-label="On this page"><span>On this page</span>' + "".join(links) + "</nav>"
    intro = INTRO.search(html)
    at = intro.end() if intro else html.index("<h2 id=")
    return html[:at] + "\n" + nav + html[at:]


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


def hype(net_home, net_away, p_home, net_sd):
    """0-100 before the game: mostly how evenly matched it is, plus a little for team quality.

    Checked on 2016-17 to 2025-26: games with a 55% and a 65% favourite finish about as close as
    each other, but blowouts climb fast once the favourite is past 70%, so the matchup part stays
    full up to 55% and drops to zero at 85%. Expected points was left out, as higher-scoring
    projections ended up slightly more lopsided, not less.
    """
    quality = team_quality(net_home, net_away, net_sd)
    favourite = max(p_home, 1 - p_home)
    matchup = min(1, max(0, (0.85 - favourite) / 0.30))
    return round(100 * (0.65 * matchup + 0.35 * quality))


# Build steps

def text_on(bg):
    """White or dark text, whichever has more contrast on a solid background colour."""
    def lum(hex_):
        c = [int(hex_.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    white, dark = "#ffffff", "#1b2230"
    L = lum(bg)
    return white if 1.05 / (L + 0.05) >= (L + 0.05) / (lum(dark) + 0.05) else dark


def team_summaries(teams, ratings, preseason, week_ago, results, stats, season_sim, games, sched, cur):
    """Everything shown about each team: ratings, ranks, schedule strength, record and simulation odds."""
    info = {}
    for i, team in enumerate(teams):
        code, slug, short = data.TEAMS.get(team, (team[:3].upper(), slugify(team), team))
        info[team] = {
            "name": team, "code": code, "slug": slug, "short": short,
            "colours": data.TEAM_COLOURS.get(team, data.DEFAULT_COLOURS),
            "on_colour": text_on(data.TEAM_COLOURS.get(team, data.DEFAULT_COLOURS)[0]),
            "o": ratings["mu"] + ratings["off"][i],
            "d": ratings["mu"] + ratings["def"][i],
            "net": ratings["off"][i] - ratings["def"][i],
            "t": ratings["t_mu"] + ratings["tempo"][i],
            "pre": (preseason["off"][i] - preseason["def"][i]) if preseason else None,
            "wk": (week_ago["off"][i] - week_ago["def"][i]) if week_ago else None,
            **{k: results[team][k] for k in ("w", "l", "pf", "pa", "luck", "waa", "gp", "cw", "cl")},
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
            game["hype"] = hype(net[row.home], net[row.away], game["pred"]["p_home"], net_sd)
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


def asset_version():
    """Short hash of the static files, added to their URLs so browsers fetch new copies after a change."""
    h = hashlib.sha1()
    for name in sorted(os.listdir(os.path.join(HERE, "static"))):
        path = os.path.join(HERE, "static", name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:8]


SITEMAP_AHEAD = pd.Timedelta(days=7)  # how far ahead game pages go in the sitemap
LIST_HOURS = 3  # check the feed for team lists for games this close to tip
WEEK_WAIT = pd.Timedelta(hours=33)  # Monday midnight to Tuesday 9am: the longest a late result is waited for


def week_end(sched, cur, now):
    """End (UTC) of the last full Monday-to-Sunday week (Sydney time) to show.

    It moves on to the week just finished once every game from it is in the data, usually a few
    hours into Monday; until then the week before stays up. If a game still hasn't arrived by
    Tuesday 9am (postponed, or a broken box score), it moves on anyway and the page says how many
    of the week's games it covers.
    """
    monday = now.tz_convert(SYDNEY).normalize()
    monday = monday - pd.Timedelta(days=monday.weekday())
    season_sched = sched[sched.start == cur]
    end = monday.tz_convert("UTC")
    due = season_sched[(season_sched.utc >= end - pd.Timedelta(days=7)) & (season_sched.utc < end)]
    if not due.done.all() and now < (monday + WEEK_WAIT).tz_convert("UTC"):
        end -= pd.Timedelta(days=7)
    return end


def week_block(games, sched, teams, cal, cur, pb, weights, avg_impact, start, end, n_sims):
    """One week's movers, finals race, best offense and defense and player of the week. Everything
    is worked out as of the Monday before and the Monday after, so it reads the same later on."""
    season_sched = sched[sched.start == cur]
    due = season_sched[(season_sched.utc >= start) & (season_sched.utc < end)]
    season = games[games.start == cur]
    week = season[(season.utc >= start) & (season.utc < end)]
    if week.empty:
        return None
    before = model.fit(games, start, cur, teams)
    after = model.fit(games, end, cur, teams)
    if not before or not after:
        return None

    def net(r, t):
        i = r["idx"][t]
        return r["off"][i] - r["def"][i]

    def ranks(r):
        return {t: k + 1 for k, t in enumerate(sorted(teams, key=lambda t: -net(r, t)))}

    rk0, rk1 = ranks(before), ranks(after)
    moves = sorted(({"team": t, "from": net(before, t), "to": net(after, t), "diff": net(after, t) - net(before, t),
                     "rk_from": rk0[t], "rk_to": rk1[t]} for t in teams), key=lambda m: -m["diff"])

    # Finals odds before and after, with the same random draws so only the week's games differ.
    def odds(r, t):
        as_of = sched.copy()
        as_of["done"] = as_of.done & (as_of.utc < t)
        return sim.simulate(r, cal, as_of, teams, n=n_sims)["teams"]

    o0, o1 = odds(before, start), odds(after, end)
    race = sorted(({"team": t, "from": o0[t]["top6"], "to": o1[t]["top6"]} for t in teams),
                  key=lambda r: -abs(r["to"] - r["from"]))[:2]

    # Each team's offense and defense in the week's games, adjusted for opponent and venue.
    played = []
    for row in week.itertuples():
        for team, opp, side, pf, pa in ((row.home, row.away, 1, row.h_pts, row.a_pts), (row.away, row.home, -1, row.a_pts, row.h_pts)):
            if team not in after["idx"] or opp not in after["idx"]:
                continue
            j = after["idx"][opp]
            side = 0 if row.neutral else side
            played.append({"team": team, "poss": row.poss,
                           "o": 100 * pf / row.poss - after["def"][j] - side * after["hfa"],
                           "d": 100 * pa / row.poss - after["off"][j] + side * after["hfa"]})
    played = pd.DataFrame(played)
    by_team = played.groupby("team").apply(lambda d: pd.Series({
        "o": np.average(d.o, weights=d.poss), "d": np.average(d.d, weights=d.poss), "n": len(d)}))
    best_o, best_d = by_team.o.idxmax(), by_team.d.idxmin()

    # Player of the week: points added above an average player over the week's games.
    wk = pb[pb.match_id.isin(week.match_id)]
    agg = wk.groupby(["player", "team"])[impact.STATS + ["on_poss", "min"]].sum()
    agg["games"] = wk.groupby(["player", "team"]).match_id.nunique()
    agg = agg[agg["min"] >= 20 * agg.games]
    potw = None
    if len(agg):
        rates = agg[impact.STATS].div(agg.on_poss.clip(lower=1), axis=0) * 100
        c = weights["coef"]
        value = weights["intercept"] + sum(c[k] * rates[k] for k in impact.STATS) + c["team_net"] * weights["team_mean"]
        added = (value - avg_impact) * agg.on_poss / 100
        (player, team) = added.idxmax()
        r = agg.loc[(player, team)]
        potw = {"player": player, "team": team, "added": float(added.max()), "games": int(r.games), "min": float(r["min"]),
                "pts": int(r.pts), "reb": int(r.orb + r.drb), "ast": int(r.ast)}

    local = start.tz_convert(SYDNEY)
    last = (end - pd.Timedelta(days=1)).tz_convert(SYDNEY)
    first = f"{local.day}" if local.month == last.month else f"{local.day} {local:%b}"
    rounds = sorted({int(r) for r in due[~due.finals].round_number.dropna()})
    return {"start": start.isoformat(), "label": f"{first}–{last.day} {last:%b}",
            "round": f"Round {rounds[0]}" if len(rounds) == 1 else f"Rounds {rounds[0]}–{rounds[-1]}" if rounds else "Finals",
            "games": len(week), "scheduled": len(due), "riser": moves[0], "faller": moves[-1], "race": race,
            "best_o": {"team": best_o, **{k: float(v) for k, v in by_team.loc[best_o].items()}},
            "best_d": {"team": best_d, **{k: float(v) for k, v in by_team.loc[best_d].items()}},
            "potw": potw}


def week_history(path, games, sched, teams, cal, cur, pb, weights, avg_impact, now, n_sims, log=print):
    """Every finished week of this season, newest first. A week is worked out once, when it first
    shows, and kept in `path` so it reads the same later on; one that went up with results still
    missing is redone when they arrive. Past seasons stay in the file for the season review."""
    saved = {}
    if path and os.path.exists(path):
        with open(path) as f:
            saved = json.load(f)
    kept = {w["start"]: w for w in saved.get(str(cur), [])}
    season_sched = sched[sched.start == cur]
    if season_sched.empty:
        return []
    first = season_sched.utc.min().tz_convert(SYDNEY).normalize()
    start = (first - pd.Timedelta(days=first.weekday())).tz_convert("UTC")
    last = week_end(sched, cur, now)
    season = games[games.start == cur]
    weeks = []
    while start < last:
        end = start + pd.Timedelta(days=7)
        w = kept.get(start.isoformat())
        played = int(((season.utc >= start) & (season.utc < end)).sum())
        if w is None or (w["games"] < w["scheduled"] and played > w["games"]):
            w = week_block(games, sched, teams, cal, cur, pb, weights, avg_impact, start, end, n_sims)
            if w:
                log(f"  week of {w['label']} added to the weekly history")
        if w:
            weeks.append(w)
        start = end
    if path:
        saved[str(cur)] = weeks
        with open(path, "w") as f:
            json.dump(saved, f, indent=1, ensure_ascii=False, default=float)
    return weeks[::-1]


def coming_up(all_games, teams, info, now):
    """Worked out fresh every build: the most watchable game in the next seven days, and the
    hardest next three games."""
    upcoming = [g for g in all_games if not g["done"] and not g["pending"] and g["utc"] >= now]
    soon = [g for g in upcoming if g["utc"] < now + pd.Timedelta(days=7)] or upcoming[:6]
    watch = max(soon, key=lambda g: g["hype"]) if soon else None
    runs = []
    for t in teams:
        nxt = [g for g in upcoming if t in (g["home"], g["away"])][:3]
        if len(nxt) == 3:
            opps = [g["away"] if g["home"] == t else g["home"] for g in nxt]
            runs.append({"team": t, "opps": opps, "avg": float(np.mean([info[o]["net"] for o in opps]))})
    tough = max(runs, key=lambda r: r["avg"]) if runs else None
    return {"watch": watch, "tough": tough}


def scouting_log(team, all_games, games, ratings, info):
    """This season's played games with each one's offense and defense adjusted for the opponent and
    venue, using the current ratings, plus the same numbers over a few splits."""
    poss = games.set_index("match_id").poss
    rows = []
    for g in all_games:
        if not g["done"] or team not in (g["home"], g["away"]) or g["id"] not in poss.index:
            continue
        home = g["home"] == team
        opp = g["away"] if home else g["home"]
        if opp not in ratings["idx"]:
            continue
        j = ratings["idx"][opp]
        side = 0 if g["neutral"] else (1 if home else -1)
        p = float(poss[g["id"]])
        pf, pa = (g["hs"], g["as"]) if home else (g["as"], g["hs"])
        raw_o, raw_d = 100 * pf / p, 100 * pa / p
        adj_o = raw_o - ratings["def"][j] - side * ratings["hfa"]
        adj_d = raw_d - ratings["off"][j] + side * ratings["hfa"]
        rows.append({"g": g, "opp": opp, "home": home, "poss": p, "pf": pf, "pa": pa, "raw_o": raw_o, "raw_d": raw_d,
                     "o": adj_o, "d": adj_d, "net": adj_o - adj_d, "opp_rk": info[opp]["rk"]["net"]})
    n = len(info)

    def split(label, picked):
        if not picked:
            return {"label": label, "n": 0}
        w = np.array([r["poss"] for r in picked])
        o = float(np.average([r["o"] for r in picked], weights=w))
        d = float(np.average([r["d"] for r in picked], weights=w))
        return {"label": label, "n": len(picked), "o": o, "d": d, "net": o - d}

    splits = [split("All games", rows), split("Home", [r for r in rows if r["home"] and not r["g"]["neutral"]]),
              split("Away", [r for r in rows if not r["home"] and not r["g"]["neutral"]]),
              split("Last 5", rows[-5:]),
              split(f"vs top {n // 2}", [r for r in rows if r["opp_rk"] <= n // 2]),
              split(f"vs bottom {n - n // 2}", [r for r in rows if r["opp_rk"] > n // 2])]
    return rows, splits


def home_away(scouting):
    """Each team's opponent-adjusted net rating at home and on the road this season, with its record.

    The league-wide home edge is already taken out of every game, so the two numbers are on the
    same scale: a team that plays the same everywhere has about the same number in both.
    """
    out = {}
    for team, (rows, _) in scouting.items():
        side = {}
        for key, at_home in (("home", True), ("road", False)):
            picked = [r for r in rows if r["home"] == at_home and not r["g"]["neutral"]]
            if not picked:
                side[key] = {"n": 0}
                continue
            w = np.array([r["poss"] for r in picked])
            side[key] = {"n": len(picked), "w": sum(r["pf"] > r["pa"] for r in picked),
                         "net": float(np.average([r["net"] for r in picked], weights=w))}
        if side["home"]["n"] and side["road"]["n"]:
            side["gap"] = side["home"]["net"] - side["road"]["net"]
        out[team] = side
    return out


VENUE_MIN_GAMES = 6  # home and road games every team needs before League today names a best


def venue_leaders(venue):
    if not venue or any(v[k]["n"] < VENUE_MIN_GAMES for v in venue.values() for k in ("home", "road")):
        return None
    return {k: max(venue, key=lambda t: venue[t][k]["net"]) for k in ("home", "road")}


def past_ratings(games, cur):
    """Final ratings for every finished season, as they stood after the last game."""
    out = []
    for start in sorted(games.start.unique()):
        if start >= cur:
            continue
        season = games[games.start == start]
        teams = sorted(set(season.home) | set(season.away))
        r = model.fit(games, season.utc.max() + pd.Timedelta(hours=1), start, teams)
        if not r:
            continue
        rows = [{"team": t, "o": r["mu"] + r["off"][i], "d": r["mu"] + r["def"][i], "net": r["off"][i] - r["def"][i],
                 "t": r["t_mu"] + r["tempo"][i]} for i, t in enumerate(teams)]
        out.append({"label": f"{start}-{str(start + 1)[-2:]}", "rows": sorted(rows, key=lambda x: -x["net"])})
    return out[::-1]


def rating_views(history, past, teams, season):
    """The tables for the past ratings page: every match day this season, newest first, then each
    past season's final ratings."""
    views = []
    for i, point in enumerate(reversed(history)):
        rows = [{"team": t, "net": point["net"][t], "o": point["o"][t], "d": point["d"][t], "t": point["t"][t]} for t in teams]
        label = f"{season} preseason" if point["label"] == "Preseason" else f"After {point['label']}"
        views.append({"id": f"d{i}", "label": label, "rows": sorted(rows, key=lambda r: -r["net"])})
    for i, s in enumerate(past):
        views.append({"id": f"s{i}", "label": f"{s['label']} final", "rows": s["rows"]})
    return views


# Percentile bars on player pages: (label, column, higher is better, format, tooltip).
# None means neither direction is better (usage is a role, not a skill), so the bar stays grey.
PERCENTILE_STATS = [
    ("Impact", "impact", True, "sgn",
     "Estimated points per 100 possessions the player adds over an average NBL player, from the box score."),
    ("Points per 40", "p40", True, "f1", "Points scored per 40 minutes on court, so starters and bench players compare fairly."),
    ("True shooting", "ts", True, "pct",
     "Scoring efficiency: points per shot, counting threes and free throws."),
    ("Usage", "usg", None, "pct",
     "How much of the offense runs through the player: the share of team possessions they finish with a shot, "
     "free throws or a turnover while on court. Around 20% is average, 30% is a go-to scorer."),
    ("Assist %", "ast_pct", True, "pct", "Share of teammates' made baskets the player assisted while on court."),
    ("Rebound %", "reb_pct", True, "pct", "Share of available rebounds the player grabbed while on court (offensive and defensive averaged)."),
    ("Steal %", "stl_pct", True, "pct", "Steals per opponent possession while on court."),
    ("Block %", "blk_pct", True, "pct", "Blocks per opponent two-point attempt while on court."),
    ("Turnover %", "tov_pct", False, "pct", "How often the player's possessions end in a turnover."),
]


def player_pages(seasons, cur, pb, info, deep_players=None, comps=None):
    pages = {}
    current = seasons[seasons.start == cur].copy()
    current["reb_pct"] = (current.orb_pct + current.drb_pct) / 2
    pool = current[(current.mpg >= 10) & (current.gp >= 3)]
    # Impact rank among each season's regulars, for the career line
    regulars = seasons[(seasons.mpg >= 10) & (seasons.gp >= 3) & seasons.impact.notna()].copy()
    regulars["rank"] = regulars.groupby("start").impact.rank(ascending=False, method="min").astype(int)
    regulars["of"] = regulars.groupby("start").impact.transform("size")
    for slug, rows in seasons.groupby("slug"):
        rows = rows.sort_values(["start", "min"])
        latest = rows.iloc[-1]
        page = {"name": latest.player, "slug": slug, "team": latest.team, "pos": latest.pos,
                "rows": rows.to_dict("records"), "current": bool((rows.start == cur).any())}
        gp = int(rows.gp.sum())
        best = regulars.loc[regulars.index.intersection(rows.index)].sort_values("impact")
        page["career"] = {"gp": gp, "seasons": int(rows.start.nunique()), "first": int(rows.start.min()),
                          "last": int(rows.start.max()), "teams": list(dict.fromkeys(rows.team[::-1]))[::-1],
                          "ppg": float((rows.ppg * rows.gp).sum() / gp) if gp else 0.0,
                          "rpg": float((rows.rpg * rows.gp).sum() / gp) if gp else 0.0,
                          "apg": float((rows.apg * rows.gp).sum() / gp) if gp else 0.0,
                          "best": best.iloc[-1].to_dict() if len(best) else None}
        if page["current"]:
            me = current[current.slug == slug].sort_values("min").iloc[-1]
            page["impact_rank"] = int((pool.impact > me.impact).sum() + 1) if me.impact == me.impact else None
            page["pool_n"] = len(pool)
            page["percentiles"] = []
            for label, col, higher, fmt, tip in PERCENTILE_STATS:
                value, others = me[col], pool[col].dropna()
                if value != value or others.empty:
                    continue
                share = (others > value).mean() if higher is False else (others < value).mean()
                page["percentiles"].append({"label": label, "value": value, "fmt": fmt, "higher": higher, "tip": tip,
                                            "pct": round(100 * share)})
            page["log_svg"] = game_log_svg(pb[(pb.player == latest.player) & (pb.start == cur)], latest.team, info)
            page["deep"] = (deep_players or {}).get((me.player, me.team))
            page["comps"] = (comps or {}).get((me.player, me.team), [])
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
    games, sched = data.load(paths, log)
    cur = int(sched.start.max())
    season = f"{cur}-{str(cur + 1)[-2:]}"
    teams = sorted(set(sched.home) | set(sched.away))
    now = pd.Timestamp.now(tz="UTC")
    log(f"{len(games)} games loaded; {season} season: {int(sched.done.sum())} played, "
        f"{int((~sched.done).sum())} to go")

    log("Backtesting ...")
    backtest, cal = model.backtest(games)
    ratings = model.fit(games, now, cur, teams)
    sanity_check(games, teams, ratings, cur)
    preseason = model.fit(games, sched.utc.min() - pd.Timedelta(hours=1), cur, teams)
    week_ago = model.fit(games, now - pd.Timedelta(days=7), cur, teams)
    stats = model.team_stats(games, cur, teams)
    player_box = data.load_players(paths)
    log("Lineups and shots from the play-by-play ...")
    deep = pbp.season_tables(paths["pbp"], games, player_box, cur, teams, log) if paths.get("pbp") else None
    for team in teams:
        if stats.get(team) and deep:
            stats[team].update(deep["stats"].get(team, {}))
    results = model.results_table(games, cur, ratings, cal, teams)
    log("Rating history ...")
    history = model.history(games, cur, teams)
    log(f"Simulating the season {n_sims:,} times ...")
    season_sim = sim.simulate(ratings, cal, sched, teams, n=n_sims)

    info = team_summaries(teams, ratings, preseason, week_ago, results, stats, season_sim, games, sched, cur)
    order = sorted(teams, key=lambda t: info[t]["rk"]["net"])

    all_games, net_sd = game_list(sched, games, info, ratings, cal, cur, teams, now)

    log("Player impact and availability ...")
    pb = impact.prepare(player_box, games)
    weights = impact.fit_weights(pb)
    values_now = impact.current_values(pb, weights, cur)
    absences = {t: impact.missing_regulars(pb, t, cur, values_now) for t in teams}
    soon = [g["id"] for g in all_games
            if not g["done"] and not g["pending"] and g["utc"] - now < pd.Timedelta(hours=LIST_HOURS)]
    lists = feed.team_lists(soon, log) if soon and any(absences.values()) else {}
    adjust_for_absences(all_games, absences, cal, lists)

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
    comps = players.similar(seasons, cur)
    rotations = players.rotation(player_box, games, cur, teams)
    log("Past ratings ...")
    past = past_ratings(games, cur)

    regular = sched[~sched.finals]
    n_games = int(max(((regular.home == t) | (regular.away == t)).sum() for t in teams))
    for team in teams:
        wins = info[team]["sim"]["wins"]
        info[team]["proj_rec"] = f"{round(wins)}-{n_games - round(wins)}"
        info[team]["likely"] = int(np.argmax(info[team]["sim"]["pos_dist"])) + 1
    standings = sorted(teams, key=lambda t: (-info[t]["w"], info[t]["l"], -info[t]["pf"] / max(info[t]["pa"], 1)))
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
              "N": len(teams), "v": asset_version()}
    site = Site(cfg, out_dir, common)

    em_max = max(abs(info[t]["net"]) for t in teams) or 1
    site.page("", "index.html", upcoming=upcoming[:6], em_max=em_max, S=season_sim)
    site.page("schedule/", "schedule.html", upcoming=upcoming, completed=completed[::-1], pending=pending,
              net_sd=net_sd)
    site.page("ladder/", "ladder.html", S=season_sim, standings=standings, projected=projected, n_games=n_games)
    regular = games[(games.start == cur) & ~games.finals]
    flow = (int(regular.h_ahead.notna().sum()) if "h_ahead" in regular else 0, len(regular))
    scouting = {t: scouting_log(t, all_games, games, ratings, info) for t in teams}
    venue = home_away(scouting)
    site.page("stats/", "stats.html", defs=model.STAT_DEFS, missing=missing_stats, flow=flow, venue=venue)
    site.page("players/", "players.html",
              players=sorted(current.to_dict("records"), key=lambda r: -r["min"]))
    site.page("matchup/", "matchup.html")
    site.page("model/", "model.html", bt=backtest, cal=cal, R=ratings, replay=season_record(completed),
              ledger=ledger)
    trend_rows = league.trends(games, cur)
    weeks_path = os.path.join(os.path.dirname(ledger_path), "weeks.json") if ledger_path else None
    weeks = week_history(weeks_path, games, sched, teams, cal, cur, pb, weights, averages.get(cur, 0.0), now, n_sims, log)
    slugs = {(r.player, r.team): r.slug for r in current.itertuples()}
    for w in weeks:
        if w["potw"]:
            w["potw"]["slug"] = slugs.get((w["potw"]["player"], w["potw"]["team"]))
    site.page("league/", "league.html", rmap=league.ratings_map_svg(info, teams, ratings["mu"], cfg.get("base_path", "/")),
              trends=[(key, label, fmt, tip, trend_rows[-1][key], trend_rows[-2][key] if len(trend_rows) > 1 else None,
                       league.trend_svg(trend_rows, key, fmt)) for key, label, fmt, tip in league.TRENDS],
              now=trend_rows[-1], n_regular=len(sched[~sched.finals]), weeks=weeks, ahead=coming_up(all_games, teams, info, now),
              venue=venue, venue_best=venue_leaders(venue))
    site.page("about/", "about.html")
    site.page("glossary/", "glossary.html")
    for team in teams:
        site.page(f"teams/{info[team]['slug']}/", "team.html", t=team, x=info[team],
                  games=[g for g in all_games if team in (g["home"], g["away"])], roster=rosters[team],
                  rot=rotations[team], slugs={r["player"]: r["slug"] for r in rosters[team]}, chart=history_svg(history, team, teams), defs=model.STAT_DEFS, S=season_sim)
        log_rows, splits = scouting[team]
        site.page(f"teams/{info[team]['slug']}/lineups/", "lineups.html", t=team, x=info[team], L=(deep or {}).get("teams", {}).get(team),
                  deep=deep, log_rows=log_rows, splits=splits, shots=((deep or {}).get("teams", {}).get(team) or {}).get("chart"), slugs={r["player"]: r["slug"] for r in rosters[team]})
    for game in all_games:
        # games more than a week away are almost empty, so keep them out of the sitemap until then
        site.page(f"games/{game['slug']}/", "game.html", listed=game["utc"] < now + SITEMAP_AHEAD, g=game, h=info[game["home"]], a=info[game["away"]],
                  defs=model.STAT_DEFS, ld=event_ld(game, info[game["home"]], info[game["away"]], cfg, cfg.get("base_path", "/")),
                  impact_cap=impact.MAX_TEAM_PTS, S=season_sim)
    site.page("history/", "history.html", views=rating_views(history, past, teams, season))
    for page in player_pages(seasons, cur, pb, info, (deep or {}).get("players", {}), comps).values():
        site.page(f"players/{page['slug']}/", "player.html", p=page)
    site.page("404.html", "404.html")
    write_sitemap(site, cfg, now, os.path.join(os.path.dirname(ledger_path), "pages.json") if ledger_path else None)
    log(f"Wrote {len(site.urls)} pages to {out_dir}")
    snapshot = model_snapshot(season, now, ratings, cal, info, teams, season_sim, upcoming, values_now, absences)
    return {"pages": len(site.urls), "backtest": backtest, "ratings": ratings, "snapshot": snapshot}


def model_snapshot(season, now, ratings, cal, info, teams, season_sim, upcoming, values_now, absences):
    """What the model says right now, for the data archive. Can't be rebuilt later because the
    data behind it gets corrected after the fact."""
    keep = ("id", "utc", "home", "away", "neutral", "round", "venue", "finals", "pred", "hype", "adjust")
    return archive.plain({
        "season": season, "date": f"{now.tz_convert('Australia/Sydney'):%Y-%m-%d}", "time": now,
        "league": {"mu": ratings["mu"], "hfa": ratings["hfa"], "t_mu": ratings["t_mu"], "cal": cal},
        "teams": {t: {"off": ratings["off"][i], "def": ratings["def"][i], "tempo": ratings["tempo"][i],
                      "net": info[t]["net"], "rank": info[t]["rk"], "sim": season_sim["teams"][t]}
                  for i, t in enumerate(teams)},
        "upcoming": [{k: g[k] for k in keep if k in g} for g in upcoming],
        "players": values_now,
        "missing": absences,
    })


def sanity_check(games, teams, ratings, cur):
    """Stop the build if the data looks broken. A failed build leaves the last good site up,
    which is better than publishing nonsense if a data file changes shape."""
    problems = []
    if not 8 <= len(teams) <= 12:
        problems.append(f"{len(teams)} teams this season")
    # Single odd games are already left out by data.load(); this is for wider breakage.
    season = games[games.start == cur]
    if len(season) and not 60 < (season.poss * 40 / season.minutes).mean() < 100:
        problems.append("average possessions per game out of range")
    if ratings is None:
        problems.append("not enough games to fit ratings")
    else:
        net = ratings["off"] - ratings["def"]
        if not 95 < ratings["mu"] < 130 or not 65 < ratings["t_mu"] < 95 or np.abs(net).max() > 30:
            problems.append(f"ratings out of range (average {ratings['mu']:.1f}, tempo {ratings['t_mu']:.1f}, "
                            f"widest net {np.abs(net).max():.1f})")
    if problems:
        raise ValueError("Data check failed, not publishing: " + "; ".join(problems))


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


def adjust_for_absences(all_games, absences, cal, lists=None):
    """Move each team's next prediction for regulars who missed its last game.

    `lists` holds game-day team lists from the feed ({game id: names}), out about an hour before
    tip. A flagged player who is named gets a green light and no penalty, one who isn't named gets
    a red light, and with no list yet it's yellow and he's assumed out as usual. When the list
    lifts a penalty, the prediction without it is kept in g["unconfirmed"] to compare later."""
    lists = lists or {}
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
        named = {slugify(n) for n in lists.get(g["id"], ())}
        out = {side: [{**a, "light": ("green" if slugify(a["player"]) in named else "red") if named else "yellow"}
                      for a in out[side]] for side in out}
        base = {k: g["pred"][k] for k in ("margin", "total")}
        if any(a["light"] == "green" for side in out for a in out[side]):
            g["unconfirmed"] = dict(g["pred"])
            _shift_for_absences(g["unconfirmed"], out, cal)  # also gives green players their would-be pts
        g["avail"] = {"home": out["home"], "away": out["away"], "listed": bool(named), "base": base,
                      "sigma": cal["sigma"]}
        out = {side: [a for a in out[side] if a["light"] != "green"] for side in out}
        shift, capped = _shift_for_absences(g["pred"], out, cal)
        g["avail"]["players"] = [{"name": a["player"], "side": side, "raw": round(a["raw"], 3), "missed": a["missed"]}
                                 for side in ("away", "home") for a in g["avail"][side]]
        if out["home"] or out["away"]:
            g["adjust"] = {"home": out["home"], "away": out["away"], "shift": shift, "capped": capped}


def _shift_for_absences(p, out, cal):
    to_pts = p["poss"] / 100 * cal["k"]
    raw = {side: sum(a["per100"] for a in out[side]) * to_pts for side in out}
    lost = {side: min(raw[side], impact.MAX_TEAM_PTS) for side in out}
    shift = lost["away"] - lost["home"]
    p["margin"] += shift
    p["h_pts"], p["a_pts"] = p["total"] / 2 + p["margin"] / 2, p["total"] / 2 - p["margin"] / 2
    p["p_home"] = model.normal_cdf(p["margin"] / cal["sigma"])
    for side in out:
        for a in out[side]:
            a["raw"] = a["per100"] * to_pts
            a["pts"] = a["raw"] * (lost[side] / raw[side] if raw[side] else 0)
    return shift, any(raw[s] > impact.MAX_TEAM_PTS for s in out)


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
            entry = {"utc": game["utc"].isoformat(), "home": game["home"], "away": game["away"],
                     "slug": game["slug"], "p_home": round(p["p_home"], 3),
                     "margin": round(p["margin"], 1), "total": round(p["total"], 1)}
            if game.get("adjust"):  # how far the injury adjustment moved the margin, to check it later
                entry["adj"] = round(game["adjust"]["shift"], 2)
            if game.get("unconfirmed"):  # same game without the team list
                entry["unconfirmed"] = {"p_home": round(game["unconfirmed"]["p_home"], 3),
                                        "margin": round(game["unconfirmed"]["margin"], 1)}
                entry["returning"] = [a["player"] for side in ("home", "away")
                                      for a in game["avail"][side] if a["light"] == "green"]
            # Only touch the entry when the prediction changes, so the file (and the repo) stays quiet.
            old = ledger.get(game["id"], {})
            if {k: v for k, v in old.items() if k != "logged"} != entry:
                ledger[game["id"]] = {**entry, "logged": now.isoformat()}
    for game in completed:
        entry = ledger.get(game["id"])
        if entry and "hs" not in entry:
            entry["hs"], entry["as"] = game["hs"], game["as"]
    # Drop games still without a result three weeks after tip-off. A postponed game keeps its id, so
    # it's logged again once it has a new date.
    stale = now - pd.Timedelta(days=21)
    ledger = {k: e for k, e in ledger.items() if "hs" in e or pd.Timestamp(e["utc"]) > stale}
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


def write_sitemap(site, cfg, now, pages_path=None):
    """lastmod is the day a page last really changed, so it's kept between builds in pages.json."""
    base = cfg.get("base_url", "").rstrip("/")
    today = now.strftime("%Y-%m-%d")
    seen = {}
    if pages_path and os.path.exists(pages_path):
        with open(pages_path, encoding="utf-8") as f:
            seen = json.load(f)
    pages = {}
    for url in site.urls:
        h = site.hashes[url]
        old = seen.get(url)
        pages[url] = old if old and old[0] == h else [h, today]
    if pages_path:
        with open(pages_path, "w", encoding="utf-8") as f:
            json.dump(pages, f, indent=0, sort_keys=True)
    with open(os.path.join(site.out_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in site.urls:
            f.write(f"  <url><loc>{base}/{url}</loc><lastmod>{pages[url][1]}</lastmod></url>\n")
        f.write("</urlset>\n")
    with open(os.path.join(site.out_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
