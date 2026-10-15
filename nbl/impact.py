"""Player Impact rating and the availability adjustment.

Impact: points per 100 possessions a player adds vs an average player, estimated from his box
score. The stat weights are fitted to on-court plus-minus from 2016-17 to 2020-21; small samples
are pulled toward bench level.

Availability: a regular who missed his team's last game is assumed out for the next one, and that
prediction moves by his value. ADJ_SCALE was tuned on 2021-22 and tested on 2023-24 onward.
"""
import numpy as np
import pandas as pd

STATS = ["pts", "fga", "fta", "fg3m", "orb", "drb", "ast", "stl", "blk", "tov", "pf"]
SOURCE = {"pts": "points", "fga": "field_goals_attempted", "fta": "free_throws_attempted",
          "fg3m": "three_pointers_made", "orb": "rebounds_offensive", "drb": "rebounds_defensive",
          "ast": "assists", "stl": "steals", "blk": "blocks", "tov": "turnovers", "pf": "fouls_personal"}
COEF_SEASONS = (2016, 2017, 2018, 2019, 2020)
RIDGE = 2000.0
REPLACEMENT = -2.0    # a bench-level player, per 100 possessions on court
PRIOR_POSS = 300      # possessions' worth of pull toward REPLACEMENT
ADJ_SCALE = 12.0      # how much the raw values move a game margin (fitted on 2021-22, tested 2023+)


def prepare(player_box, games):
    """Player-game rows with per-possession inputs. `player_box` is data.load_players()."""
    pb = player_box.copy()
    for k, col in SOURCE.items():
        pb[k] = pd.to_numeric(pb[col], errors="coerce").fillna(0)
    pb["pm"] = pd.to_numeric(pb.plus_minus_points, errors="coerce").fillna(0)
    g = games[["match_id", "utc", "poss", "minutes", "home", "away", "h_pts", "a_pts"]].rename(
        columns={"minutes": "g_minutes"})
    pb = pb.merge(g, on="match_id", how="inner")
    pb["on_poss"] = pb.poss * pb["min"] / pb.g_minutes.clip(lower=40)
    return pb


def fit_weights(pb):
    """Ridge regression of on-court plus-minus per 100 on per-100 box stats, by player-season."""
    d = pb[pb.start.isin(COEF_SEASONS)]
    agg = d.groupby(["player", "team", "start"])[STATS + ["pm", "on_poss"]].sum()
    agg = agg[agg.on_poss >= 150]
    team_net = {}
    for (team, start), grp in d.drop_duplicates(["match_id", "team"]).groupby(["team", "start"]):
        margin = np.where(grp.team == grp.home, grp.h_pts - grp.a_pts, grp.a_pts - grp.h_pts)
        team_net[(team, start)] = 100 * margin.sum() / grp.poss.sum()
    X = agg[STATS].div(agg.on_poss, axis=0) * 100
    X["team_net"] = [team_net[(t, s)] for _, t, s in agg.index]
    y = 100 * agg.pm / agg.on_poss
    w = agg.on_poss.values
    mu = (X.values * w[:, None]).sum(0) / w.sum()
    Xc = X.values - mu
    beta = np.linalg.solve(Xc.T @ (Xc * w[:, None]) + RIDGE * np.eye(Xc.shape[1]),
                           Xc.T @ (w * (y.values - np.average(y, weights=w))))
    return {"coef": dict(zip(X.columns, beta)), "intercept": np.average(y, weights=w) - mu @ beta,
            "team_mean": mu[-1]}


def _value(agg, weights):
    rates = agg[STATS].div(agg.on_poss.clip(lower=1), axis=0) * 100
    c = weights["coef"]
    box = weights["intercept"] + sum(c[k] * rates[k] for k in STATS) + c["team_net"] * weights["team_mean"]
    n = agg.on_poss
    return (box * n + REPLACEMENT * PRIOR_POSS) / (n + PRIOR_POSS)


def current_values(pb, weights, cur):
    """Each player's value now: this season, plus last season at half weight."""
    d = pb[pb.start >= cur - 1].copy()
    d["w"] = np.where(d.start == cur, 1.0, 0.5)
    for k in STATS + ["on_poss"]:
        d[k] = d[k] * d.w
    return _value(d.groupby("player")[STATS + ["on_poss"]].sum(), weights)


def season_values(pb, weights):
    """Each player's value for each season on its own, for the season-by-season tables."""
    return _value(pb.groupby(["player", "team", "start"])[STATS + ["on_poss"]].sum(), weights)


def league_average(values_by_season, pb, start):
    """Minute-weighted average value in a season, so Impact can be shown relative to it."""
    poss = pb[pb.start == start].groupby(["player", "team", "start"]).on_poss.sum()
    v = values_by_season.reindex(poss.index)
    ok = v.notna()
    return float(np.average(v[ok], weights=poss[ok])) if ok.any() else 0.0


def missing_regulars(pb, team, cur, values):
    """Regulars (3+ of the team's last 5 games, 12+ minutes) who didn't play its most recent game."""
    season = pb[(pb.start == cur) & (pb.team == team)]
    recent_ids = season.drop_duplicates("match_id").sort_values("utc").match_id.tail(5)
    if recent_ids.empty:
        return []
    recent = season[season.match_id.isin(recent_ids)]
    stats = recent.groupby("player")["min"].agg(["count", "mean"])
    regulars = stats[(stats["count"] >= 3) & (stats["mean"] >= 12)]
    last_game = set(season[season.match_id == recent_ids.iloc[-1]].player)
    out = []
    for player in regulars.index:
        if player in last_game:
            continue
        # Missing someone no better than a bench player doesn't count (tested: slightly more accurate).
        value = float(values.get(player, REPLACEMENT))
        per100 = ADJ_SCALE * max(value - REPLACEMENT, 0) * regulars.loc[player, "mean"] / 200
        if per100 >= 0.2:
            out.append({"player": player, "mpg": float(regulars.loc[player, "mean"]), "per100": per100})
    return sorted(out, key=lambda r: -r["per100"])
