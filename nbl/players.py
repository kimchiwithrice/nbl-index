"""Player season stats, including possession-based rates (usage, true shooting and so on)."""
import re
import unicodedata

import numpy as np
import pandas as pd

from .model import team_games


def slugify(text):
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def player_seasons(player_box, games):
    """One row per player per team per season, with totals, per-game numbers and rates.

    Rates that depend on team or opponent totals (usage, rebound %, etc.) use the totals from
    the games the player appeared in, scaled by the share of minutes he was on court.
    """
    team_totals = team_games(games)[["match_id", "team", "minutes", "poss", "o_fga", "o_fta", "o_tov", "o_fgm",
                                     "o_orb", "o_drb", "d_orb", "d_drb", "d_fg2a", "d_fga", "d_fta", "d_tov",
                                     "o_pts"]]
    rows = player_box.merge(team_totals, on=["match_id", "team"], how="inner")
    # Both tables have a "minutes" column; the team one (minutes_y) is game length.
    rows["tm_min"] = rows.minutes_y * 5 if "minutes_y" in rows else rows["minutes"] * 5

    s = rows.groupby(["player", "team", "start"]).agg(
        gp=("match_id", "nunique"),
        gs=("starter", lambda x: int(pd.to_numeric(x, errors="coerce").fillna(0).sum())),
        min=("min", "sum"), pts=("points", "sum"),
        fgm=("field_goals_made", "sum"), fga=("field_goals_attempted", "sum"),
        fg3m=("three_pointers_made", "sum"), fg3a=("three_pointers_attempted", "sum"),
        ftm=("free_throws_made", "sum"), fta=("free_throws_attempted", "sum"),
        orb=("rebounds_offensive", "sum"), drb=("rebounds_defensive", "sum"), ast=("assists", "sum"),
        tov=("turnovers", "sum"), stl=("steals", "sum"), blk=("blocks", "sum"), pf=("fouls_personal", "sum"),
        pm=("plus_minus_points", "sum"),
        tm_min=("tm_min", "sum"), tm_fga=("o_fga", "sum"), tm_fta=("o_fta", "sum"), tm_tov=("o_tov", "sum"),
        tm_fgm=("o_fgm", "sum"), tm_orb=("o_orb", "sum"), tm_drb=("o_drb", "sum"),
        op_orb=("d_orb", "sum"), op_drb=("d_drb", "sum"), op_2pa=("d_fg2a", "sum"), op_poss=("poss", "sum"),
        pos=("playing_position", lambda x: x.dropna().astype(str).mode().iat[0] if x.notna().any() else ""),
    ).reset_index()

    minutes = s["min"].clip(lower=1)
    on_court = minutes / (s.tm_min / 5).clip(lower=1)  # share of team minutes on court
    used = s.fga + 0.44 * s.fta + s.tov                 # possessions used

    def safe(denominator):
        return denominator.replace(0, np.nan)

    s["mpg"] = s["min"] / s.gp
    s["ppg"] = s.pts / s.gp
    s["rpg"] = (s.orb + s.drb) / s.gp
    s["apg"] = s.ast / s.gp
    s["p40"] = 40 * s.pts / minutes
    s["r40"] = 40 * (s.orb + s.drb) / minutes
    s["a40"] = 40 * s.ast / minutes
    s["ts"] = s.pts / safe(2 * (s.fga + 0.44 * s.fta))
    s["efg"] = (s.fgm + 0.5 * s.fg3m) / safe(s.fga)
    s["fg3"] = s.fg3m / safe(s.fg3a)
    s["ft"] = s.ftm / safe(s.fta)
    s["fg3r"] = s.fg3a / safe(s.fga)
    s["usg"] = used / safe(on_court * (s.tm_fga + 0.44 * s.tm_fta + s.tm_tov))
    s["ast_pct"] = s.ast / safe(on_court * s.tm_fgm - s.fgm)
    s["orb_pct"] = s.orb / safe(on_court * (s.tm_orb + s.op_drb))
    s["drb_pct"] = s.drb / safe(on_court * (s.tm_drb + s.op_orb))
    s["stl_pct"] = s.stl / safe(on_court * s.op_poss)
    s["blk_pct"] = s.blk / safe(on_court * s.op_2pa)
    s["tov_pct"] = s.tov / safe(used)
    s["pm40"] = 40 * s.pm / minutes
    s["ortg"] = 100 * s.pts / safe(used)  # points per 100 possessions used
    s["slug"] = s.player.map(slugify)
    return s


def assign_slugs(seasons):
    """Give every player a unique URL slug. Players with the same name get their first team added."""
    first_team = seasons.sort_values("start").groupby("player").team.first()
    names_by_slug = {}
    for name in seasons.player.unique():
        names_by_slug.setdefault(slugify(name), []).append(name)
    slugs = {}
    for slug, names in names_by_slug.items():
        if len(names) == 1:
            slugs[names[0]] = slug
        else:
            for name in names:
                slugs[name] = slug + "-" + slugify(first_team[name])
    # If two players still clash (same name and same first team), number them.
    seen = {}
    for name in sorted(slugs):
        slug = slugs[name]
        if slug in seen:
            seen[slug] += 1
            slugs[name] = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 1
    seasons["slug"] = seasons.player.map(slugs)
    return seasons


# Style comparisons: rates that describe how someone plays rather than how much.
STYLE = ["usg", "ts", "fg3r", "ftr", "ast_pct", "orb_pct", "drb_pct", "stl_pct", "blk_pct", "tov_pct"]
STYLE_MIN = 300  # minutes in a past season to count as a comparison
STYLE_MIN_NOW = 150  # minutes this season before we try


def similar(seasons, cur, n=5):
    """For each player this season, the past player-seasons closest in style.

    Each rate is put on the same scale (standard deviations from the average), then the nearest
    seasons by straight-line distance are picked. A player's own seasons are left out, and each other player appears once.
    Returns {(player, team): [rows]}.
    """
    s = seasons.copy()
    s["ftr"] = s.fta / s.fga.replace(0, np.nan)
    pool = s[(s["min"] >= STYLE_MIN) & (s.start < cur)].dropna(subset=STYLE)
    now = s[(s["min"] >= STYLE_MIN_NOW) & (s.start == cur)].dropna(subset=STYLE)
    if pool.empty or now.empty:
        return {}
    mean, sd = pool[STYLE].mean(), pool[STYLE].std()
    a = ((pool[STYLE] - mean) / sd).to_numpy()
    out = {}
    for row in now.itertuples():
        me = ((pd.Series({k: getattr(row, k) for k in STYLE}) - mean) / sd).to_numpy()
        dist = np.sqrt(((a - me) ** 2).mean(axis=1))
        order, seen = [], {row.player}
        for i in np.argsort(dist):  # one season per player
            if pool.player.iat[i] not in seen:
                seen.add(pool.player.iat[i])
                order.append(i)
            if len(order) == n:
                break
        out[(row.player, row.team)] = [{"player": pool.player.iat[i], "slug": pool.slug.iat[i], "team": pool.team.iat[i],
                                        "start": int(pool.start.iat[i]), "dist": float(dist[i]),
                                        "ppg": float(pool.ppg.iat[i])} for i in order]
    return out


ROTATION_MIN = 10  # minutes a game, over all the team's games, to count as part of the regular rotation


def rotation(player_box, games, cur, teams, recent=3):
    """How each team shares out its minutes this season.

    A player's share is his minutes out of all the team's minutes (five on court at a time), so a
    full game every night is 20% and missed games bring it down. Returns {team: {...}}, with the
    top-5 and top-8 shares, how many players average ROTATION_MIN minutes over all the team's
    games, and a row per player with his share overall and over the last `recent` games.
    """
    season = games[games.start == cur].sort_values("utc")
    box = player_box[player_box.match_id.isin(set(season.match_id))]
    floor_min = season.set_index("match_id").minutes * 5
    out = {}
    for team in teams:
        ids = list(season.match_id[(season.home == team) | (season.away == team)])
        mine = box[box.team == team]
        if not ids or mine.empty:
            out[team] = None
            continue
        last = ids[-recent:]
        rows = mine.groupby("player").agg(
            gp=("match_id", "nunique"),
            gs=("starter", lambda x: int(pd.to_numeric(x, errors="coerce").fillna(0).sum())),
            min=("min", "sum"))
        rows["share"] = rows["min"] / floor_min[ids].sum()
        rows["recent"] = (mine[mine.match_id.isin(last)].groupby("player")["min"].sum()
                          .reindex(rows.index).fillna(0) / floor_min[last].sum())
        rows["mpg"] = rows["min"] / rows.gp
        rows = rows.sort_values("share", ascending=False)
        out[team] = {
            "top5": float(rows.share.head(5).sum()), "top8": float(rows.share.head(8).sum()),
            "regulars": int((rows["min"] / len(ids) >= ROTATION_MIN).sum()),
            "games": len(ids), "recent": len(last),
            "rows": [{"player": p, "gp": int(r.gp), "gs": int(r.gs), "mpg": float(r.mpg), "share": float(r.share),
                      "recent": float(r.recent)} for p, r in rows.iterrows()],
        }
    # League rank and average for the top-5 share, most top-heavy first.
    done = [t for t in teams if out[t]]
    for i, t in enumerate(sorted(done, key=lambda t: -out[t]["top5"])):
        out[t]["rank"] = i + 1
    for key in ("top5", "top8", "regulars"):
        avg = float(np.mean([out[t][key] for t in done])) if done else None
        for t in done:
            out[t][key + "_avg"] = avg
    return out
