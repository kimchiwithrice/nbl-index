"""Play-by-play: who was on the floor, and what happened while they were.

The NBL feed logs every substitution, so the five players on court for each team can be rebuilt
event by event. Every game is checked before it's used: each team must have exactly five on court
the whole game, nobody can do anything while on the bench, the points must add up to the final
score, and each player's time on court must match their minutes in the box score. Games that fail
any check are left out of everything built from lineups. Checked against the official box scores,
the rebuilt plus-minus matches for 96-100% of player-games from 2017-18 on. Before that too many
games fail, so lineups start in 2017-18.

The feed comes in two shapes. Up to 2024-25 events are listed newest first with lower-case names
("freethrow", "drivinglayup"); from 2025-26 some games are listed oldest first with spaced names
("freeThrow", "Driving Layup"). Both are put into one form here.
"""
import numpy as np
import pandas as pd

from . import data, rds, shotchart

COLUMNS = ["match_id", "team_name", "period", "period_type", "gt", "action_type", "sub_type", "success",
           "first_name", "family_name", "qualifier", "x", "y", "s1", "s2"]
# Things only a player on the floor can do.
# Assists are left out: the feed often logs them a few seconds after the basket, by which time the
# passer may have been subbed off.
ON_COURT = {"2pt", "3pt", "freethrow", "rebound", "turnover", "steal", "block", "foulon"}
POINTS = {"2pt": 2, "3pt": 3, "freethrow": 1}
MINUTES_SLACK = 60  # seconds a player's rebuilt time may differ from the box score
FIRST_SEASON = 2017  # lineups from 2017-18; earlier seasons fail the checks too often

# Shot types as the feed names them, grouped. Checked against shot locations in 2025-26: the rim
# group's median distance is about 1 m, floaters and hooks about 2-3 m.
RIM = {"layup", "drivinglayup", "reverselayup", "dunk", "tipin", "tipinlayup", "tipindunk", "alleyoop",
       "alleyoopdunk", "eurostep"}
FLOATERS = {"floatingjumpshot", "hookshot"}
KINDS = [("rim", "Layups & dunks"), ("float", "Floaters & hooks"), ("jumper", "2-point jumpers"), ("three", "3-pointers")]
ZONES = [("rim", "At the rim"), ("short", "Short (1.5-3 m)"), ("mid", "Mid-range"), ("corner", "Corner 3"),
         ("above", "Above-the-break 3")]
CLUTCH_SECS = 300    # last five minutes of the fourth quarter and overtime ...
CLUTCH_MARGIN = 5    # ... with the score within five


def _norm(series):
    return series.fillna("").astype(str).str.lower().str.replace(r"[^a-z0-9]", "", regex=True)


def load_events(path, games):
    """Events for the games in `games`, in the order they happened, one row each.

    Adds t (seconds since tip-off), side ('h' or 'a'), player, act, sub (normalised names) and pts.
    """
    pbp = rds.read_frame(path, COLUMNS)
    pbp["match_id"] = pbp.match_id.astype(str)
    pbp = pbp[pbp.match_id.isin(set(games.match_id)) & (pbp.period >= 1)].copy()
    pbp["row"] = np.arange(len(pbp))
    clock = pbp["gt"].str.split(":", expand=True).astype(float)
    left = clock[0] * 60 + clock[1]
    ot = pbp.period_type.eq("OVERTIME")
    pbp["t"] = np.where(ot, 2400 + pbp.period * 300, pbp.period * 600) - left
    pbp["q"] = np.where(ot, 4 + pbp.period, pbp.period).astype(int)
    pbp = pbp[pbp.t.notna()]  # only period and fixture markers come without a clock

    # Work out which way each game is listed: the clock should mostly move forward.
    step = pbp.groupby("match_id").t.diff()
    forward = (step > 0).groupby(pbp.match_id).sum() > (step < 0).groupby(pbp.match_id).sum()
    pbp["n"] = np.where(pbp.match_id.map(forward), pbp.row, -pbp.row)
    pbp = pbp.sort_values(["match_id", "t", "n"], kind="stable").reset_index(drop=True)

    sides = games.set_index("match_id")[["home", "away"]]
    team = pbp.team_name.map(data.clean_name)
    pbp["side"] = np.select([team == pbp.match_id.map(sides.home), team == pbp.match_id.map(sides.away)],
                            ["h", "a"], "")
    pbp["player"] = (pbp.first_name.fillna("").astype(str).str.strip() + " "
                     + pbp.family_name.fillna("").astype(str).str.strip()).str.strip()
    pbp["act"] = _norm(pbp.action_type)
    pbp["sub"] = _norm(pbp.sub_type)
    # Newer games log the fouled player as a foul with sub-type "drawn".
    drawn = (pbp.act == "foul") & (pbp["sub"] == "drawn")
    pbp.loc[drawn, "act"] = "foulon"
    pbp["made"] = pbp.success.fillna(0).astype(float) > 0
    pbp["pts"] = np.where(pbp.made, pbp.act.map(POINTS).fillna(0), 0).astype(int)
    pbp["qual"] = pbp.qualifier.fillna("").astype(str).str.lower()
    # The newer feed sometimes logs the same substitution twice in a row.
    subs = pbp.act == "substitution"
    twice = subs & subs.shift() & (pbp[["match_id", "t", "player", "sub"]] == pbp[["match_id", "t", "player", "sub"]].shift()).all(axis=1)
    pbp = pbp[~twice]
    return pbp[["match_id", "t", "q", "side", "player", "act", "sub", "made", "pts", "qual", "x", "y"]]


def _period_start(events, before):
    """The five a team started a period with: everyone whose first event in the period isn't
    coming on, topped up with whoever was on at the end of the last period."""
    first = {}
    for act, sub, player in events:
        if player and player not in first and (act in ON_COURT or act == "substitution"):
            first[player] = "in" if (act == "substitution" and sub == "in") else "on"
    start = [p for p, how in first.items() if how == "on"]
    for p in before:
        if len(start) >= 5:
            break
        if p not in first:
            start.append(p)
    return start


def game_stints(g, starters):
    """Rebuild one game. Returns (stints, problem). `starters` is {side: [names]} from the box score.

    A stint is a stretch with no substitutions: both lineups, start and end time, and each team's
    points, field goal attempts, offensive rebounds, turnovers and free throw attempts.
    """
    on = {}
    stints = []
    rows = list(zip(g.t, g.q, g.side, g.player, g.act, g["sub"], g.pts))
    by_period = {}
    for r in rows:
        by_period.setdefault(r[1], []).append(r)
    end_of_last = {s: list(starters.get(s, [])) for s in "ha"}
    blank = {f"{s}_{k}": 0 for s in "ha" for k in ("pts", "fga", "orb", "tov", "fta")}

    for q in sorted(by_period):
        period = by_period[q]
        q_start = 600 * (q - 1) if q <= 4 else 2400 + 300 * (q - 5)
        skip = set()
        for s in "ha":
            # Older games log the starting five as five subs at the opening second.
            opening = [i for i, r in enumerate(period) if r[2] == s and r[0] == q_start and r[4] == "substitution"]
            if len(opening) == 5 and all(period[i][5] == "in" for i in opening):
                on[s] = [period[i][3] for i in opening]
                skip.update(opening)
            else:
                on[s] = _period_start([(r[4], r[5], r[3]) for r in period if r[2] == s], end_of_last[s])
            if len(on[s]) != 5:
                return [], f"period {q}: {len(on[s])} on court for {s}"
        cur = {"h": tuple(sorted(on["h"])), "a": tuple(sorted(on["a"])), "start": q_start, **blank}
        went_off = {}  # player -> when they were subbed off, so a free throw logged after the sub still counts
        for i, (t, _, side, player, act, sub, pts) in enumerate(period):
            if i in skip:
                continue
            if act == "substitution" and side:
                if sub == "out":
                    if player not in on[side]:
                        return [], f"{player} subbed off while on the bench"
                    on[side].remove(player)
                    went_off[player] = t
                elif sub == "in":
                    if player in on[side]:
                        return [], f"{player} subbed on while already on"
                    on[side].append(player)
                lineup = tuple(sorted(on[side]))
                if lineup != cur[side] and len(on["h"]) == 5 and len(on["a"]) == 5:
                    if t > cur["start"] or any(cur[k] for k in blank):
                        stints.append({**cur, "end": t})
                    cur = {"h": tuple(sorted(on["h"])), "a": tuple(sorted(on["a"])), "start": t, **blank}
                continue
            if not side:
                continue
            if player and act in ON_COURT and player not in on[side] and went_off.get(player) != t:
                return [], f"{player} did something ({act}) while on the bench"
            if len(on[side]) != 5:
                return [], f"{len(on[side])} on court for {side}"
            if act in POINTS:
                cur[f"{side}_pts"] += pts
                if act == "freethrow":
                    cur[f"{side}_fta"] += 1
                else:
                    cur[f"{side}_fga"] += 1
            elif act == "rebound" and sub == "offensive":
                cur[f"{side}_orb"] += 1
            elif act == "turnover":
                cur[f"{side}_tov"] += 1
        q_end = q_start + (600 if q <= 4 else 300)
        if len(on["h"]) != 5 or len(on["a"]) != 5:
            return [], f"period {q} ended with the wrong number on court"
        if q_end > cur["start"] or any(cur[k] for k in blank):
            stints.append({**cur, "end": q_end})
        end_of_last = {s: list(on[s]) for s in "ha"}
    return stints, None


def rebuild(events, games, player_box):
    """Stints for every game that passes the checks, plus a table of which games passed and why not."""
    box = player_box[["match_id", "team", "player", "min", "starter", "points"]].copy()
    box["starter"] = pd.to_numeric(box.starter, errors="coerce").fillna(0) > 0
    sides = games.set_index("match_id")[["home", "away", "h_pts", "a_pts", "minutes", "start"]]
    box = box[box.match_id.isin(sides.index)]
    box["side"] = np.where(box.team == box.match_id.map(sides.home), "h",
                           np.where(box.team == box.match_id.map(sides.away), "a", ""))
    box_by_game = dict(tuple(box.groupby("match_id")))

    all_stints, report = [], []
    for match_id, g in events.groupby("match_id", sort=False):
        info = sides.loc[match_id]
        b = box_by_game.get(match_id)
        problem = None
        if b is None:
            problem = "no box score"
        else:
            starters = {s: list(b[(b.side == s) & b.starter].player) for s in "ha"}
            stints, problem = game_stints(g, starters)
        if not problem:
            st = pd.DataFrame(stints)
            if st.h_pts.sum() != info.h_pts or st.a_pts.sum() != info.a_pts:
                problem = f"points {st.h_pts.sum()}-{st.a_pts.sum()} vs final {info.h_pts:.0f}-{info.a_pts:.0f}"
            elif st.end.max() != info.minutes * 60:
                problem = "doesn't run to the end of the game"
        if not problem:
            secs = {}
            for s in "ha":
                for lineup, dur in zip(st[s], st.end - st.start):
                    for p in lineup:
                        secs[(s, p)] = secs.get((s, p), 0) + dur
            box_secs = {(s, p): m * 60 for s, p, m in zip(b.side, b.player, b["min"])}
            worst = max(abs(secs.get(k, 0) - box_secs.get(k, 0)) for k in set(secs) | set(box_secs))
            if worst > MINUTES_SLACK:
                problem = f"minutes off by up to {worst:.0f}s"
        report.append({"match_id": match_id, "start": int(info.start), "ok": problem is None, "problem": problem})
        if not problem:
            st["match_id"] = match_id
            all_stints.append(st)
    stints = pd.concat(all_stints, ignore_index=True) if all_stints else pd.DataFrame()
    return stints, pd.DataFrame(report)


# Shots

def _kind(act, sub):
    if act == "3pt":
        return "three"
    if sub in RIM:
        return "rim"
    return "float" if sub in FLOATERS else "jumper"


def _zone(x, y, act):
    """Court zone from the shot's position (only logged from 2025-26). x and y are percentages of a
    28 x 15 m court; the basket is 1.575 m in from each baseline."""
    if x != x or y != y:
        return None
    across, along = y / 100 * 15, x / 100 * 28
    from_baseline = min(along, 28 - along)
    dist = np.hypot(from_baseline - 1.575, across - 7.5)
    if act == "3pt":
        return "corner" if from_baseline < 3.0 else "above"
    if dist < 1.5:
        return "rim"
    return "short" if dist < 3.0 else "mid"


def shots(events):
    """Every field goal attempt: who took it, what kind, where from (when known) and, for makes,
    whether it was assisted.

    The feed logs an assist as its own event just after the basket, sometimes a few seconds later.
    An assist that follows free throws rather than a basket is a FIBA free-throw assist and isn't
    linked to any shot.
    """
    out = []
    for match_id, g in events.groupby("match_id", sort=False):
        last_make = {}
        for t, side, player, act, sub, made, x, y in zip(g.t, g.side, g.player, g.act, g["sub"], g.made, g.x, g.y):
            if not side:
                continue
            if act in ("2pt", "3pt"):
                u, v = shotchart.half_court(x, y)
                row = {"match_id": match_id, "side": side, "player": player, "three": act == "3pt", "made": made,
                       "kind": _kind(act, sub), "zone": _zone(x, y, act), "assisted": False, "t": t,
                       "u": u, "v": v, "zone10": shotchart.zone(u, v, act == "3pt")}
                out.append(row)
                if made:
                    last_make[side] = row
            elif act == "freethrow" and made:
                last_make.pop(side, None)
            elif act == "assist":
                row = last_make.pop(side, None)
                if row and row["player"] != player and t - row["t"] <= 10:
                    row["assisted"] = True
    return pd.DataFrame(out).drop(columns="t")


# Clutch

def game_extras(events):
    """Per game and side: points, possessions and seconds in clutch time, the last five minutes of
    the fourth quarter or overtime with the score within five.

    (A transition stat built from the clock was tried too. It tracked the official fast-break points
    well up to 2024-25, but the newer feed logs rebounds and shots a few seconds apart differently,
    which halves it from 2025-26 on, so it was dropped.)
    """
    rows = {}
    for match_id, g in events.groupby("match_id", sort=False):
        score = {"h": 0, "a": 0}
        r = {f"{s}_{k}": 0.0 for s in "ha" for k in ("c_pts", "c_fga", "c_orb", "c_tov", "c_fta")}
        r["c_secs"] = 0.0
        prev_t, prev_clutch = 0.0, False
        for t, q, side, act, sub, pts in zip(g.t, g.q, g.side, g.act, g["sub"], g.pts):
            if prev_clutch:
                r["c_secs"] += t - prev_t
            q_end = 2400 if q <= 4 else 2400 + 300 * (q - 4)
            clutch = q >= 4 and q_end - t <= CLUTCH_SECS and abs(score["h"] - score["a"]) <= CLUTCH_MARGIN
            prev_t, prev_clutch = t, clutch
            if not side:
                continue
            if act in POINTS:
                if clutch:
                    r[f"{side}_c_pts"] += pts
                    r[f"{side}_c_{'fta' if act == 'freethrow' else 'fga'}"] += 1
                score[side] += pts
            elif clutch and act == "rebound" and sub == "offensive":
                r[f"{side}_c_orb"] += 1
            elif clutch and act == "turnover":
                r[f"{side}_c_tov"] += 1
        rows[match_id] = r
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("match_id").reset_index()


# Lineups

def _poss(df, side):
    return df[f"{side}_fga"] - df[f"{side}_orb"] + df[f"{side}_tov"] + 0.44 * df[f"{side}_fta"]


def team_stints(stints, games, start):
    """One row per team per stint in a season: the five, seconds, possessions, points for and against."""
    if stints.empty:
        return pd.DataFrame()
    g = games[games.start == start].set_index("match_id")
    st = stints[stints.match_id.isin(g.index)]
    if st.empty:
        return pd.DataFrame()
    poss = (_poss(st, "h") + _poss(st, "a")) / 2
    parts = []
    for side, other, team in (("h", "a", "home"), ("a", "h", "away")):
        parts.append(pd.DataFrame({"match_id": st.match_id.values, "team": st.match_id.map(g[team]).values,
                                   "five": st[side].values, "secs": (st.end - st.start).values,
                                   "poss": poss.values, "pf": st[f"{side}_pts"].values,
                                   "pa": st[f"{other}_pts"].values}))
    return pd.concat(parts, ignore_index=True)


def _rates(secs, poss, pf, pa):
    poss = max(poss, 1e-9)
    return {"min": secs / 60, "poss": poss, "o": 100 * pf / poss, "d": 100 * pa / poss, "net": 100 * (pf - pa) / poss}


LINEUP_MIN = 10   # minutes together before a five is listed
PAIR_MIN = 20     # ... or a pair
ONOFF_MIN = 20    # minutes on court (and on the bench) before on/off is shown
RANK_MIN = 100    # minutes on court before a player is ranked on on/off


def lineup_tables(ts, team, top=10):
    """Most-used fives and pairs for a team, and each player's on/off numbers."""
    mine = ts[ts.team == team]
    if mine.empty:
        return None
    total = mine[["secs", "poss", "pf", "pa"]].sum()
    fives = mine.groupby("five")[["secs", "poss", "pf", "pa"]].sum().sort_values("secs", ascending=False)
    fives = fives[fives.secs >= 60 * LINEUP_MIN]
    lineups = [{"players": list(k), **_rates(*v)} for k, v in fives.head(top).iterrows()]

    pairs, onoff = {}, {}
    for five, secs, poss, pf, pa in zip(mine.five, mine.secs, mine.poss, mine.pf, mine.pa):
        for i, p in enumerate(five):
            acc = onoff.setdefault(p, [0.0, 0.0, 0.0, 0.0])
            acc[0] += secs; acc[1] += poss; acc[2] += pf; acc[3] += pa
            for q in five[i + 1:]:
                acc = pairs.setdefault((p, q), [0.0, 0.0, 0.0, 0.0])
                acc[0] += secs; acc[1] += poss; acc[2] += pf; acc[3] += pa
    pairs = [{"players": list(k), **_rates(*v)} for k, v in sorted(pairs.items(), key=lambda kv: -kv[1][0])[:top]
             if v[0] >= 60 * PAIR_MIN]
    players = {}
    for p, (secs, poss, pf, pa) in onoff.items():
        if secs < 60 * ONOFF_MIN:
            continue
        on = _rates(secs, poss, pf, pa)
        off = _rates(total.secs - secs, total.poss - poss, total.pf - pf, total.pa - pa)
        # a handful of bench minutes is too noisy to compare against
        enough = off["min"] >= ONOFF_MIN
        players[p] = {"on": on, "off": off if enough else None,
                      "diff": (on["net"] - off["net"]) if enough else None}
    return {"lineups": lineups, "pairs": pairs, "onoff": players, "games": int(mine.match_id.nunique()),
            "min": float(total.secs / 60)}


# Everything the site shows from the play-by-play, for one season

def _shot_rows(sh, key_col, keys):
    """FGA, share of attempts and FG% for each kind or zone."""
    total = len(sh)
    rows = []
    for key, label in keys:
        part = sh[sh[key_col] == key]
        rows.append({"key": key, "label": label, "fga": len(part), "share": len(part) / total if total else None,
                     "pct": part.made.mean() if len(part) else None})
    return rows


def season_tables(path, games, player_box, cur, teams, log=print):
    """Team stats, team page tables and player page numbers built from this season's play-by-play.
    Returns None if the play-by-play can't be read."""
    season = games[games.start == cur]
    try:
        events = load_events(path, season)
    except Exception as e:
        log(f"  couldn't read the play-by-play ({e}); skipping lineups and shots")
        return None
    box = player_box[player_box.match_id.isin(set(season.match_id))]
    stints, report = rebuild(events, season, box)
    ok_games = int(report.ok.sum()) if len(report) else 0
    log(f"  lineups rebuilt for {ok_games} of {len(season)} games")

    sides = season.set_index("match_id")[["home", "away", "finals", "poss"]]
    sh = shots(events)
    if not sh.empty:
        sh["team"] = np.where(sh.side == "h", sh.match_id.map(sides.home), sh.match_id.map(sides.away))
        sh["opp"] = np.where(sh.side == "h", sh.match_id.map(sides.away), sh.match_id.map(sides.home))
    ex = game_extras(events).set_index("match_id")
    ts = team_stints(stints, season, cur)
    has_zones = not sh.empty and sh.zone.notna().any()
    league = shotchart.league_averages(sh) if has_zones else {}

    out = {"stats": {}, "teams": {}, "players": {}, "games": ok_games, "of": len(season),
           "pbp_games": int(events.match_id.nunique()), "zones": bool(has_zones)}
    regular = sides[~sides.finals.astype(bool)]
    for team in teams:
        mine = sh[(sh.team == team) & sh.match_id.isin(regular.index)] if not sh.empty else sh
        theirs = sh[(sh.opp == team) & sh.match_id.isin(regular.index)] if not sh.empty else sh
        stats = {}
        if len(mine) and len(theirs):
            for kind in ("rim", "float", "jumper"):
                stats[f"{kind}_r"] = (mine.kind == kind).mean()
                stats[kind] = mine[mine.kind == kind].made.mean() if (mine.kind == kind).any() else None
            stats["d_rim_r"] = (theirs.kind == "rim").mean()
            stats["d_rim"] = theirs[theirs.kind == "rim"].made.mean() if (theirs.kind == "rim").any() else None
            stats["astd"] = mine[mine.made].assisted.mean() if mine.made.any() else None

        # Clutch: regular season games with a play-by-play.
        g = regular[(regular.home == team) | (regular.away == team)].join(ex, how="inner")
        if len(g):
            home = g.home == team
            pick = lambda col: np.where(home, g["h_" + col], g["a_" + col])  # noqa: E731
            other = lambda col: np.where(home, g["a_" + col], g["h_" + col])  # noqa: E731
            c_poss = ((pick("c_fga") - pick("c_orb") + pick("c_tov") + 0.44 * pick("c_fta"))
                      + (other("c_fga") - other("c_orb") + other("c_tov") + 0.44 * other("c_fta"))) / 2
            stats["c_min"] = g.c_secs.sum() / 60
            stats["c_net"] = 100 * (pick("c_pts").sum() - other("c_pts").sum()) / c_poss.sum() if c_poss.sum() >= 20 else None
        out["stats"][team] = {k: (None if v is None or v != v else float(v)) for k, v in stats.items()}

        all_mine = sh[sh.team == team] if not sh.empty else sh
        all_theirs = sh[sh.opp == team] if not sh.empty else sh
        out["teams"][team] = {
            "lineups": lineup_tables(ts, team) if not ts.empty else None,
            "kinds": list(zip(_shot_rows(all_mine, "kind", KINDS), _shot_rows(all_theirs, "kind", KINDS))) if len(all_mine) else [],
            "zones": list(zip(_shot_rows(all_mine, "zone", ZONES), _shot_rows(all_theirs, "zone", ZONES))) if has_zones and len(all_mine) else [],
            "chart": {"mine": shotchart.svg(all_mine, league, 1, f"{team} shots"),
                      "theirs": shotchart.svg(all_theirs, league, -1, f"Opponents' shots against {team}"),
                      "n_mine": len(all_mine), "n_theirs": len(all_theirs)} if has_zones and len(all_mine) else None,
        }

    if not sh.empty:
        for (player, team), mine in sh.groupby(["player", "team"]):
            if not player:
                continue
            makes = mine[mine.made]
            lt = out["teams"].get(team, {}).get("lineups")
            out["players"][(player, team)] = {
                "fga": len(mine), "kinds": _shot_rows(mine, "kind", KINDS),
                "zones": _shot_rows(mine, "zone", ZONES) if mine.zone.notna().any() else [],
                "astd": makes.assisted.mean() if len(makes) else None,
                "astd3": makes[makes.three].assisted.mean() if makes.three.any() else None,
                "onoff": (lt or {}).get("onoff", {}).get(player),
            }
    # Players who were on the floor but never took a shot still get their on/off numbers.
    for team, tables in out["teams"].items():
        for player, onoff in ((tables["lineups"] or {}).get("onoff") or {}).items():
            out["players"].setdefault((player, team), {"fga": 0, "kinds": [], "zones": [], "astd": None,
                                                       "astd3": None, "onoff": onoff})
    # League rank on on/off, among players with enough minutes both on and off the court.
    ranked = [r for t in out["teams"].values() if t["lineups"] for r in t["lineups"]["onoff"].values()
              if r["diff"] is not None and r["on"]["min"] >= RANK_MIN]
    ranked.sort(key=lambda r: -r["diff"])
    for i, r in enumerate(ranked):
        r["rank"], r["rank_n"] = i + 1, len(ranked)
    return out
