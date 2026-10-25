"""Adjusted efficiency ratings, game predictions, the backtest and team stats.

Ratings are points per 100 possessions against an average NBL team on a neutral court:

    points per 100 (A vs B) = league average + A's offense + B's defense +/- home court
    possessions per 40 min  = league average + A's tempo + B's tempo

All teams are fitted together with ridge regression, which adjusts every rating for the
opponents faced. Games are weighted by age (exponential decay), and last season's games are
included at a discount so early-season ratings aren't built on a handful of games.
"""
import math

import numpy as np
import pandas as pd

HALF_LIFE_DAYS = 120       # a game's weight halves every 120 days
PRIOR_SEASON_WEIGHT = 0.8  # extra discount for last season's games (rosters change)
RIDGE_EFF = 6.0            # shrinkage towards average, roughly in games' worth of evidence
RIDGE_TEMPO = 6.0
REFIT_DAYS = 7             # how often the backtest refits

DEFAULT_CAL = {"k": 1.0, "sigma": 12.0, "tot_bias": 0.0, "sigma_tot": 16.0}


def ridge(X, y, weights, penalty):
    XtW = X.T * weights
    return np.linalg.solve(XtW @ X + np.diag(penalty), XtW @ y)


def fit(games, asof, cur_start, teams, half_life=HALF_LIFE_DAYS):
    """Fit ratings using games played before `asof` (a UTC timestamp).

    Returns None if there aren't enough games yet. `teams` fixes the order of the rating arrays.
    """
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    g = games[(games.utc < asof) & (games.start >= cur_start - 1)
              & games.home.isin(idx) & games.away.isin(idx)]
    if len(g) < n:
        return None
    age_days = (asof - g.utc).dt.total_seconds().values / 86400
    weight = 0.5 ** (age_days / half_life) * np.where(g.start.values < cur_start, PRIOR_SEASON_WEIGHT, 1.0)
    home_i, away_i = g.home.map(idx).values, g.away.map(idx).values
    home_side = np.where(g.neutral.values, 0.0, 1.0)
    poss = g.poss.values
    n_games = len(g)
    rows = np.arange(n_games)

    # Efficiency: one row for each team's offense against the other team's defense.
    # Columns: intercept, home court, offense ratings (n), defense ratings (n).
    X = np.zeros((2 * n_games, 2 + 2 * n))
    X[:, 0] = 1
    X[:n_games, 1], X[n_games:, 1] = home_side, -home_side
    X[rows, 2 + home_i] = 1
    X[rows, 2 + n + away_i] = 1
    X[n_games + rows, 2 + away_i] = 1
    X[n_games + rows, 2 + n + home_i] = 1
    y = np.concatenate([100 * g.h_pts.values / poss, 100 * g.a_pts.values / poss])
    penalty = np.full(2 + 2 * n, RIDGE_EFF)
    penalty[:2] = 0
    b = ridge(X, y, np.concatenate([weight, weight]), penalty)
    off, de = b[2:2 + n], b[2 + n:]
    mu = b[0] + off.mean() + de.mean()

    # Tempo: possessions per 40 minutes, with both teams contributing.
    T = np.zeros((n_games, 1 + n))
    T[:, 0] = 1
    T[rows, 1 + home_i] += 1
    T[rows, 1 + away_i] += 1
    tempo_penalty = np.full(1 + n, RIDGE_TEMPO)
    tempo_penalty[0] = 0
    bt = ridge(T, poss * 40 / g.minutes.values, weight, tempo_penalty)
    tempo = bt[1:]

    return {
        "teams": teams, "idx": idx, "mu": float(mu), "hfa": float(b[1]),
        "off": off - off.mean(), "def": de - de.mean(),
        "t_mu": float(bt[0] + 2 * tempo.mean()), "tempo": tempo - tempo.mean(),
        "n_games": int(n_games),
    }


def expected(ratings, home, away, neutral=False):
    """Expected home points, away points and possessions for a 40-minute game."""
    h, a = ratings["idx"][home], ratings["idx"][away]
    side = 0.0 if neutral else 1.0
    poss = ratings["t_mu"] + ratings["tempo"][h] + ratings["tempo"][a]
    home_eff = ratings["mu"] + side * ratings["hfa"] + ratings["off"][h] + ratings["def"][a]
    away_eff = ratings["mu"] - side * ratings["hfa"] + ratings["off"][a] + ratings["def"][h]
    return poss * home_eff / 100, poss * away_eff / 100, poss


def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def predict(ratings, cal, home, away, neutral=False):
    """Prediction for one game. `cal` holds the error sizes measured in the backtest."""
    home_pts, away_pts, poss = expected(ratings, home, away, neutral)
    margin = (home_pts - away_pts) * cal["k"]
    total = home_pts + away_pts + cal["tot_bias"]
    return {"home": home, "away": away, "h_pts": total / 2 + margin / 2, "a_pts": total / 2 - margin / 2,
            "margin": margin, "total": total, "poss": poss, "p_home": normal_cdf(margin / cal["sigma"])}


# Backtest

def backtest(games, half_life=HALF_LIFE_DAYS, first_start=None):
    """Replay past seasons: refit every REFIT_DAYS using only earlier games, predict the next block.

    Returns (summary, calibration).
    """
    starts = sorted(games.start.unique())
    first_start = first_start or starts[1]
    test = games[games.start >= first_start]
    records = []
    day = test.utc.min().normalize()
    end = test.utc.max()
    while day <= end:
        block = test[(test.utc >= day) & (test.utc < day + pd.Timedelta(days=REFIT_DAYS))]
        if len(block):
            cur = int(block.start.max())
            recent = games[games.start >= cur - 1]
            teams = sorted(set(recent.home) | set(recent.away))
            ratings = fit(games, day, cur, teams, half_life)
            if ratings:
                for row in block.itertuples():
                    if row.home in ratings["idx"] and row.away in ratings["idx"]:
                        home_pts, away_pts, _ = expected(ratings, row.home, row.away, row.neutral)
                        records.append((row.utc, home_pts - away_pts, home_pts + away_pts,
                                        row.h_pts - row.a_pts, row.h_pts + row.a_pts, row.ot))
        day += pd.Timedelta(days=REFIT_DAYS)
    if not records:
        return None, dict(DEFAULT_CAL)
    # pm/pt = predicted margin/total, am/at = actual margin/total
    df = pd.DataFrame(records, columns=["utc", "pm", "pt", "am", "at", "ot"])
    return summarize(df, half_life)


def fit_calibration(df):
    k = float((df.pm * df.am).sum() / (df.pm ** 2).sum())  # least-squares slope through the origin
    k = min(max(k, 0.7), 1.6)
    sigma = float(np.sqrt(np.mean((df.am - k * df.pm) ** 2)))
    bias = float(np.mean(df["at"] - df.pt))
    sigma_total = float(np.std(df["at"] - df.pt - bias))
    return {"k": k, "sigma": sigma, "tot_bias": bias, "sigma_tot": sigma_total}


def log_loss(df, cal):
    p = np.array([normal_cdf(cal["k"] * m / cal["sigma"]) for m in df.pm])
    y = (df.am > 0).astype(float).values
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), p, y


def summarize(df, half_life):
    df = df.sort_values("utc").reset_index(drop=True)

    # Fit the margin stretch on the first half and check it on the second half. It's only kept
    # if it improves predictions on games it wasn't fitted to.
    half = len(df) // 2
    raw = {"k": 1.0, "sigma": float(np.sqrt(np.mean((df.am[:half] - df.pm[:half]) ** 2))),
           "tot_bias": 0.0, "sigma_tot": 16.0}
    oos_raw, _, _ = log_loss(df[half:], raw)
    oos_cal, _, _ = log_loss(df[half:], fit_calibration(df[:half]))
    cal = fit_calibration(df)
    if oos_cal >= oos_raw:
        cal["k"] = 1.0
        cal["sigma"] = float(np.sqrt(np.mean((df.am - df.pm) ** 2)))

    ll, p, y = log_loss(df, cal)
    home_rate = y.mean()
    base_ll = float(-np.mean(y * np.log(home_rate) + (1 - y) * np.log(1 - home_rate)))
    fav_p = np.maximum(p, 1 - p)
    fav_won = np.where(p >= 0.5, y, 1 - y)
    calib = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)):
        in_bin = (fav_p >= lo) & (fav_p < hi)
        if in_bin.sum():
            calib.append({"bin": f"{int(lo * 100)}-{min(int(hi * 100), 100)}%", "n": int(in_bin.sum()),
                          "pred": float(fav_p[in_bin].mean()), "act": float(fav_won[in_bin].mean())})

    summary = {
        "games": int(len(df)), "from": df.utc.min().date().isoformat(), "to": df.utc.max().date().isoformat(),
        "logloss": ll, "base_logloss": base_ll, "home_rate": float(home_rate),
        "acc": float(np.mean((p >= 0.5) == (y == 1))),
        "mae_margin": float(np.mean(np.abs(df.am - cal["k"] * df.pm))),
        "mae_total": float(np.mean(np.abs(df["at"] - df.pt - cal["tot_bias"]))),
        "oos_raw": oos_raw, "oos_cal": oos_cal, "calib": calib, "half_life": half_life,
        "by_season": [],
    }
    df["season"] = df.utc.dt.year.where(df.utc.dt.month >= 7, df.utc.dt.year - 1)
    for start, season_df in df.groupby("season"):
        season_ll, season_p, season_y = log_loss(season_df, cal)
        summary["by_season"].append({
            "season": f"{start}-{str(start + 1)[-2:]}", "n": int(len(season_df)), "logloss": season_ll,
            "acc": float(np.mean((season_p >= 0.5) == (season_y == 1)))})
    return summary, cal


# Team stats

def team_games(games):
    """One row per team per game: the team's box totals (o_*) and its opponent's (d_*)."""
    cols = [c[2:] for c in games.columns if c.startswith("h_")]
    base = games[["match_id", "utc", "start", "neutral", "ot", "minutes", "poss", "finals"]]
    home = base.copy()
    home["team"], home["opp"] = games.home.values, games.away.values
    away = base.copy()
    away["team"], away["opp"] = games.away.values, games.home.values
    for c in cols:
        home["o_" + c], home["d_" + c] = games["h_" + c].values, games["a_" + c].values
        away["o_" + c], away["d_" + c] = games["a_" + c].values, games["h_" + c].values
    return pd.concat([home, away], ignore_index=True)


# (key, label, higher is better (None = neither), table group, format)
STAT_DEFS = [
    ("efg", "eFG%", True, "o4", "pct"), ("tov", "TO%", False, "o4", "pct"),
    ("orb", "OR%", True, "o4", "pct"), ("ftr", "FTRate", True, "o4", "pct"),
    ("d_efg", "eFG%", False, "d4", "pct"), ("d_tov", "TO%", True, "d4", "pct"),
    ("d_orb", "OR%", False, "d4", "pct"), ("d_ftr", "FTRate", False, "d4", "pct"),
    ("fg3", "3P%", True, "sh", "pct"), ("fg2", "2P%", True, "sh", "pct"), ("ft", "FT%", True, "sh", "pct"),
    ("fg3r", "3PA rate", None, "sh", "pct"), ("d_fg3", "Opp 3P%", False, "sh", "pct"),
    ("d_fg2", "Opp 2P%", False, "sh", "pct"), ("d_ft", "Opp FT%", False, "sh", "pct"),
    ("d_fg3r", "Opp 3PA rate", False, "sh", "pct"),
    ("ast", "Ast/FGM", True, "st", "pct"), ("d_ast", "Opp Ast/FGM", False, "st", "pct"),
    ("stl", "Steal%", True, "st", "pct"), ("blk", "Block%", True, "st", "pct"),
    ("paint", "Paint pts/100", True, "st", "1"), ("d_paint", "Opp paint pts/100", False, "st", "1"),
    ("fbp", "Fast-break pts/100", True, "st", "1"), ("d_fbp", "Opp fast-break/100", False, "st", "1"),
    ("scp", "2nd-chance pts/100", True, "st", "1"), ("pot", "Pts off TO/100", True, "st", "1"),
    ("bench", "Bench pts share", None, "st", "pct"),
    ("raw_o", "Raw off. eff.", True, "raw", "1"), ("raw_d", "Raw def. eff.", False, "raw", "1"),
    ("raw_t", "Raw tempo", None, "raw", "1"),
    ("ahead", "Time leading", True, "gf", "pct"), ("level", "Time tied", None, "gf", "pct"),
    ("behind", "Time trailing", False, "gf", "pct"),
    # From the play-by-play (pbp.py), added in build.py
    ("rim_r", "Layups", None, "sp", "pct"), ("rim", "Layup FG%", True, "sp", "pct"),
    ("float_r", "Floaters", None, "sp", "pct"), ("jumper_r", "2pt jumpers", None, "sp", "pct"),
    ("jumper", "Jumper FG%", True, "sp", "pct"), ("astd", "Assisted", None, "sp", "pct"),
    ("d_rim_r", "Opp layups", False, "sp", "pct"), ("d_rim", "Opp layup FG%", False, "sp", "pct"),
    ("c_min", "Clutch minutes", None, "cl", "1"), ("c_net", "Clutch net/100", True, "cl", "1"),
]


def team_stats(games, cur_start, teams):
    """Season-to-date regular season stats for each team (not opponent-adjusted)."""
    rows = team_games(games[(games.start == cur_start) & ~games.finals])
    rows = rows[rows.team.isin(teams)]
    out = {}
    for team in teams:
        mine = rows[rows.team == team]
        if mine.empty:
            out[team] = None
            continue
        tot = mine.sum(numeric_only=True)
        poss = tot.poss

        def per100(col):
            # Some splits are missing from the feed in recent seasons.
            return None if mine[col].isna().all() else 100 * tot[col] / poss

        s = {
            "efg": (tot.o_fgm + 0.5 * tot.o_fg3m) / tot.o_fga,
            "tov": tot.o_tov / poss,
            "orb": tot.o_orb / (tot.o_orb + tot.d_drb),
            "ftr": tot.o_fta / tot.o_fga,
            "d_efg": (tot.d_fgm + 0.5 * tot.d_fg3m) / tot.d_fga,
            "d_tov": tot.d_tov / poss,
            "d_orb": tot.d_orb / (tot.d_orb + tot.o_drb),
            "d_ftr": tot.d_fta / tot.d_fga,
            "fg3": tot.o_fg3m / max(tot.o_fg3a, 1),
            "fg2": tot.o_fg2m / max(tot.o_fg2a, 1),
            "ft": tot.o_ftm / max(tot.o_fta, 1),
            "fg3r": tot.o_fg3a / tot.o_fga,
            "d_fg3": tot.d_fg3m / max(tot.d_fg3a, 1),
            "d_fg2": tot.d_fg2m / max(tot.d_fg2a, 1),
            "d_ft": tot.d_ftm / max(tot.d_fta, 1),
            "d_fg3r": tot.d_fg3a / tot.d_fga,
            "ast": tot.o_ast / max(tot.o_fgm, 1),
            "d_ast": tot.d_ast / max(tot.d_fgm, 1),
            "stl": tot.o_stl / poss,
            "blk": tot.o_blk / max(tot.d_fg2a, 1),
            "paint": per100("o_paint"), "fbp": per100("o_fbp"), "scp": per100("o_scp"), "pot": per100("o_pot"),
            "d_paint": per100("d_paint"), "d_fbp": per100("d_fbp"),
            "bench": None if mine.o_bench.isna().all() else tot.o_bench / max(tot.o_pts, 1),
            "raw_o": 100 * tot.o_pts / poss,
            "raw_d": 100 * tot.d_pts / poss,
            "raw_t": float((mine.poss * 40 / mine.minutes).mean()),
            "gp": int(len(mine)),
        }
        # Share of game time spent ahead, level and behind, from games with a usable play-by-play.
        flow = mine[["o_ahead", "o_level", "d_ahead"]].dropna() if "o_ahead" in mine else mine.iloc[:0]
        secs = flow.to_numpy().sum()
        s["ahead"], s["level"], s["behind"] = (flow.sum() / secs).tolist() if secs else (None, None, None)
        out[team] = {k: (None if v is None else float(v)) for k, v in s.items()}
    return out


def ranks(values, higher_is_better):
    """{team: value} -> {team: rank}, 1 being best. Missing values are left unranked."""
    items = [(t, v) for t, v in values.items() if v is not None and not (isinstance(v, float) and math.isnan(v))]
    items.sort(key=lambda kv: -kv[1] if higher_is_better in (True, None) else kv[1])
    return {t: i for i, (t, _) in enumerate(items, 1)}


def results_table(games, cur_start, ratings, cal, teams):
    """Record, points, luck and résumé for each team this regular season.

    luck = wins - wins expected from the ratings for the games played
    waa  = wins - wins an average team would expect against the same opponents and venues
    """
    played = games[(games.start == cur_start) & ~games.finals]
    out = {t: {"w": 0, "l": 0, "pf": 0, "pa": 0, "exp_w": 0.0, "avg_w": 0.0} for t in teams}
    for row in played.itertuples():
        for team, opp, pf, pa, home in ((row.home, row.away, row.h_pts, row.a_pts, True),
                                        (row.away, row.home, row.a_pts, row.h_pts, False)):
            if team not in out or opp not in ratings["idx"]:
                continue
            rec = out[team]
            rec["w" if pf > pa else "l"] += 1
            rec["pf"] += pf
            rec["pa"] += pa
            p_home = predict(ratings, cal, row.home, row.away, row.neutral)["p_home"]
            rec["exp_w"] += p_home if home else 1 - p_home
            # An average team (all ratings zero) playing the same opponent at the same venue.
            j = ratings["idx"][opp]
            side = 0 if row.neutral else (1 if home else -1)
            poss = ratings["t_mu"] + ratings["tempo"][j]
            margin = poss * (side * 2 * ratings["hfa"] + ratings["def"][j] - ratings["off"][j]) / 100 * cal["k"]
            rec["avg_w"] += normal_cdf(margin / cal["sigma"])
    for rec in out.values():
        rec["luck"] = rec["w"] - rec["exp_w"]
        rec["waa"] = rec["w"] - rec["avg_w"]
        rec["gp"] = rec["w"] + rec["l"]
    return out


def history(games, cur_start, teams, half_life=HALF_LIFE_DAYS):
    """Each team's net rating after every match day this season, starting with the preseason rating."""
    season = games[games.start == cur_start]
    if season.empty:
        return []
    days = sorted(season.utc.dt.tz_convert("Australia/Sydney").dt.normalize().unique())
    checkpoints = [("Preseason", season.utc.min() - pd.Timedelta(hours=1))]
    checkpoints += [(d.strftime("%d %b"), (d + pd.Timedelta(days=1)).tz_convert("UTC")) for d in days]
    points = []
    for label, asof in checkpoints:
        ratings = fit(games, asof, cur_start, teams, half_life)
        if ratings:
            points.append({"label": label, "asof": asof,
                           "net": {t: float(ratings["off"][i] - ratings["def"][i]) for i, t in enumerate(teams)},
                           "o": {t: float(ratings["mu"] + ratings["off"][i]) for i, t in enumerate(teams)},
                           "d": {t: float(ratings["mu"] + ratings["def"][i]) for i, t in enumerate(teams)},
                           "t": {t: float(ratings["t_mu"] + ratings["tempo"][i]) for i, t in enumerate(teams)}})
    return points
