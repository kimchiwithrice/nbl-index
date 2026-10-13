"""Season simulator: plays out the rest of the regular season and the finals many times.

NBL finals format (since 2023-24), top six teams:
    3rd v 4th, single game. The winner plays 2nd in the semi-finals.
    5th v 6th, single game. The winner plays the loser of 3v4; that winner plays 1st.
    Semi-finals are best of three, the championship series best of five.

We assume the higher seed hosts single games and games 1, 3 and 5 of a series.

Each simulated season also shifts every team's rating by a random amount, because the ratings
are estimates. The spread of that shift narrows as more of the season is played.
"""
import numpy as np

from .model import expected

N_SIMS = 10000


def normal_cdf(x):
    """Vectorised normal CDF (Abramowitz and Stegun 7.1.26), so we don't need scipy."""
    t = 1 / (1 + 0.3275911 * np.abs(x) / np.sqrt(2))
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
             + 0.254829592) * t * np.exp(-(x / np.sqrt(2)) ** 2)
    return 0.5 * (1 + np.sign(x) * y)


def home_win_prob(ratings, cal, shift, home, away, neutral=False):
    """Home win probability in each simulation, given that simulation's rating shifts."""
    home_pts, away_pts, poss = expected(ratings, home, away, neutral)
    h, a = ratings["idx"][home], ratings["idx"][away]
    margin = (home_pts - away_pts + poss * (shift[:, h] - shift[:, a]) / 100) * cal["k"]
    return normal_cdf(margin / cal["sigma"])


def simulate(ratings, cal, sched, teams, n=N_SIMS, seed=7):
    rng = np.random.default_rng(seed)
    n_teams = len(teams)
    index = {t: i for i, t in enumerate(teams)}
    regular = sched[~sched.finals]
    played = regular[regular.done]
    remaining = regular[~regular.done & regular.home.isin(index) & regular.away.isin(index)]

    share_played = len(played) / max(len(regular), 1)
    shift_sd = 3.5 * (1 - 0.6 * share_played)  # points per 100 possessions
    shift = rng.normal(0, shift_sd, size=(n, n_teams))

    wins = np.zeros((n, n_teams))
    point_diff = np.zeros((n, n_teams))  # ladder tie-breaker
    for row in played.itertuples():
        if row.home in index and row.away in index:
            home_won = row.h_pts > row.a_pts
            wins[:, index[row.home]] += home_won
            wins[:, index[row.away]] += 1 - home_won
            point_diff[:, index[row.home]] += row.h_pts - row.a_pts
            point_diff[:, index[row.away]] += row.a_pts - row.h_pts
    for row in remaining.itertuples():
        p = home_win_prob(ratings, cal, shift, row.home, row.away, row.neutral)
        home_won = rng.random(n) < p
        wins[:, index[row.home]] += home_won
        wins[:, index[row.away]] += ~home_won
        margin = np.where(home_won, 1, -1) * rng.uniform(1, 15, n)
        point_diff[:, index[row.home]] += margin
        point_diff[:, index[row.away]] -= margin

    # Ladder: wins, then points difference, then a random tiebreak.
    sort_key = wins * 10000 + point_diff + rng.random((n, n_teams))
    order = np.argsort(-sort_key, axis=1)  # order[s, k] = index of the team finishing k+1 in season s
    position = np.empty_like(order)
    position[np.arange(n)[:, None], order] = np.arange(n_teams)[None, :]

    out = {}
    for i, team in enumerate(teams):
        out[team] = {
            "wins": float(wins[:, i].mean()), "pos": float(position[:, i].mean() + 1),
            "p1": float((position[:, i] == 0).mean()),
            "top2": float((position[:, i] <= 1).mean()),
            "top6": float((position[:, i] <= 5).mean()),
            "semis": 0.0, "gf": 0.0, "champ": 0.0,
            "pos_dist": np.bincount(position[:, i], minlength=n_teams).tolist(),
        }

    if n_teams >= 6 and len(regular):
        simulate_finals(ratings, cal, teams, order, shift, rng, out)
    for team in teams:
        out[team]["pos_dist"] = [count / n for count in out[team]["pos_dist"]]
    return {"teams": out, "n": n, "games_left": int(len(remaining)), "sd": shift_sd}


def play_series(ratings, cal, shift_row, high_seed, low_seed, best_of, rng):
    """Play a best-of-`best_of` series and return the winner."""
    needed = best_of // 2 + 1
    wins = {high_seed: 0, low_seed: 0}
    game = 0
    while max(wins.values()) < needed:
        home, away = (high_seed, low_seed) if game % 2 == 0 else (low_seed, high_seed)
        p = home_win_prob(ratings, cal, shift_row, home, away)[0]
        wins[home if rng.random() < p else away] += 1
        game += 1
    return high_seed if wins[high_seed] == needed else low_seed


def simulate_finals(ratings, cal, teams, order, shift, rng, out):
    n = order.shape[0]
    counts = {t: {"semis": 0, "gf": 0, "champ": 0} for t in teams}
    for s in range(n):
        seeds = [teams[j] for j in order[s, :6]]
        first, second, third, fourth, fifth, sixth = seeds
        shift_row = shift[s:s + 1]

        winner_34 = play_series(ratings, cal, shift_row, third, fourth, 1, rng)
        loser_34 = fourth if winner_34 == third else third
        winner_56 = play_series(ratings, cal, shift_row, fifth, sixth, 1, rng)
        play_in = play_series(ratings, cal, shift_row, loser_34, winner_56, 1, rng)
        for team in (first, second, winner_34, play_in):
            counts[team]["semis"] += 1

        finalist_a = play_series(ratings, cal, shift_row, first, play_in, 3, rng)
        finalist_b = play_series(ratings, cal, shift_row, second, winner_34, 3, rng)
        counts[finalist_a]["gf"] += 1
        counts[finalist_b]["gf"] += 1
        if seeds.index(finalist_a) < seeds.index(finalist_b):
            high, low = finalist_a, finalist_b
        else:
            high, low = finalist_b, finalist_a
        counts[play_series(ratings, cal, shift_row, high, low, 5, rng)]["champ"] += 1

    for team in teams:
        for key in ("semis", "gf", "champ"):
            out[team][key] = counts[team][key] / n
