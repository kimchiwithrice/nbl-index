"""The league today page: the ratings map and league-wide trends by season."""
import math

import numpy as np

from . import data

# (key, label, format, tooltip)
TRENDS = [
    ("ortg", "Points per 100 possessions", "1", "League-wide scoring efficiency: all points ÷ all possessions × 100."),
    ("pace", "Pace", "1", "Possessions per 40 minutes, averaged over every game."),
    ("fg3r", "3PA rate", "pct", "Share of all field-goal attempts that were threes."),
    ("home", "Home win %", "pct", "Share of games won by the home team."),
]


def season_label(start):
    return f"{start}-{str(start + 1)[-2:]}"


def trends(games, cur):
    """League totals for each regular season, this one so far included."""
    rows = []
    for start, d in games[~games.finals].groupby("start"):
        pts = d.h_pts + d.a_pts
        fga = (d.h_fga + d.a_fga).sum()
        rows.append({"start": int(start), "label": season_label(int(start)), "n": len(d), "now": start == cur,
                     "ortg": float(100 * pts.sum() / (2 * d.poss.sum())),
                     "pace": float((40 * d.poss / d.minutes).mean()),
                     "fg3r": float((d.h_fg3a + d.a_fg3a).sum() / fga),
                     "home": float((d.h_pts > d.a_pts).mean())})
    return rows


def trend_svg(rows, key, fmt, width=440, height=150):
    """Small line chart of one league number by season. This season is a hollow dot, as it isn't finished."""
    if len(rows) < 2:
        return ""
    left, right, top, bottom = 40, 22, 10, 22
    vals = [r[key] for r in rows]
    scale = 100 if fmt == "pct" else 1
    step = 2 if fmt == "pct" else (2 if max(vals) - min(vals) < 8 else 5)
    lo = math.floor(min(vals) * scale / step) * step
    hi = math.ceil(max(vals) * scale / step) * step
    if hi == lo:
        hi = lo + step
    if (hi - lo) / step > 4:
        step *= 2
        lo, hi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    n = len(rows)

    def x(i):
        return left + (width - left - right) * i / (n - 1)

    def y(v):
        return top + (height - top - bottom) * (hi - v * scale) / (hi - lo)

    def show(v):
        return f"{100 * v:.1f}%" if fmt == "pct" else f"{v:.1f}"

    svg = [f'<svg viewBox="0 0 {width} {height}" class="chart trend" role="img" aria-label="{key} by season">']
    for v in range(int(lo), int(hi) + 1, step):
        yy = top + (height - top - bottom) * (hi - v) / (hi - lo)
        svg.append(f'<line x1="{left}" x2="{width - right}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{yy + 4:.1f}" class="ax" text-anchor="end">{v}{"%" if fmt == "pct" else ""}</text>')
    for i in (0, n // 2, n - 1):
        svg.append(f'<text x="{x(i):.1f}" y="{height - 5}" class="ax" text-anchor="middle">{rows[i]["label"][2:]}</text>')
    done = [(i, r) for i, r in enumerate(rows) if not r["now"]]
    svg.append('<polyline class="ln" points="' + " ".join(f"{x(i):.1f},{y(r[key]):.1f}" for i, r in done) + '"/>')
    if rows[-1]["now"] and done:
        i, r = done[-1]
        svg.append(f'<line class="ln so" x1="{x(i):.1f}" y1="{y(r[key]):.1f}" x2="{x(n - 1):.1f}" y2="{y(rows[-1][key]):.1f}"/>')
    for i, r in enumerate(rows):
        note = f", {r['n']} games so far" if r["now"] else ""
        svg.append(f'<circle class="{"now" if r["now"] else "pt"}" cx="{x(i):.1f}" cy="{y(r[key]):.1f}" r="{4 if r["now"] else 2.6}">'
                   f'<title>{r["label"]}: {show(r[key])}{note}</title></circle>')
    svg.append("</svg>")
    return "".join(svg)


def ratings_map_svg(info, teams, mu, root="/", width=540, height=460):
    """Every team placed by AdjO (across) and AdjD (up, flipped so better defense is higher).

    The dashed lines are the league average, so the four corners are the four kinds of team.
    """
    left, right, top, bottom = 58, 16, 16, 36
    o = {t: info[t]["o"] for t in teams}
    d = {t: info[t]["d"] for t in teams}
    span = max(max(abs(v - mu) for v in o.values()), max(abs(v - mu) for v in d.values()), 4) + 2
    span = math.ceil(span / 2) * 2
    lo, hi = mu - span, mu + span

    def x(v):
        return left + (width - left - right) * (v - lo) / (hi - lo)

    def y(v):  # low AdjD (good) at the top
        return top + (height - top - bottom) * (v - lo) / (hi - lo)

    svg = [f'<svg viewBox="0 0 {width} {height}" class="chart rmap" role="img" '
           f'aria-label="Every team by adjusted offense and adjusted defense">']
    step = 2 if span <= 8 else 4
    first = math.ceil(lo / step) * step
    for v in range(int(first), int(hi) + 1, step):
        svg.append(f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="{top}" y2="{height - bottom}" class="grid"/>'
                   f'<text x="{x(v):.1f}" y="{height - bottom + 15}" class="ax" text-anchor="middle">{v}</text>'
                   f'<line x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{y(v) + 4:.1f}" class="ax" text-anchor="end">{v}</text>')
    svg.append(f'<line x1="{x(mu):.1f}" x2="{x(mu):.1f}" y1="{top}" y2="{height - bottom}" class="avg"/>'
               f'<line x1="{left}" x2="{width - right}" y1="{y(mu):.1f}" y2="{y(mu):.1f}" class="avg"/>')
    svg.append(f'<text x="{(left + width - right) / 2:.1f}" y="{height - 4}" class="ax tt" text-anchor="middle">AdjO, points scored per 100 →</text>'
               f'<text x="14" y="{(top + height - bottom) / 2:.1f}" class="ax tt" text-anchor="middle" '
               f'transform="rotate(-90 14 {(top + height - bottom) / 2:.1f})">AdjD, points allowed per 100 (fewer is higher) →</text>')
    pad = 8
    for tx, ty, anchor, label in ((width - right - pad, top + 16, "end", "Good at both ends"),
                                  (left + pad, top + 16, "start", "Defense first"),
                                  (width - right - pad, height - bottom - pad, "end", "Offense first"),
                                  (left + pad, height - bottom - pad, "start", "Struggling at both ends")):
        svg.append(f'<text x="{tx}" y="{ty}" class="quad" text-anchor="{anchor}">{label}</text>')

    # Each code label goes right of its dot if there's room, otherwise left, below or above.
    # Boxes are sized for the larger phone font so labels don't collide there either.
    dots = [(x(o[t]), y(d[t])) for t in teams]
    boxes, spots = [], {}
    lw, lh = 48, 20
    for t in sorted(teams, key=lambda t: -info[t]["net"]):
        cx, cy = x(o[t]), y(d[t])
        tries = [(cx + 11, cy + 6, "start"), (cx - 11, cy + 6, "end"), (cx, cy + 26, "middle"), (cx, cy - 12, "middle")]
        for lx, ly, anchor in tries:
            bx = {"start": lx, "end": lx - lw, "middle": lx - lw / 2}[anchor]
            box = (bx, ly - lh + 4, bx + lw, ly + 4)
            hit = any(b[0] < box[2] and box[0] < b[2] and b[1] < box[3] and box[1] < b[3] for b in boxes)
            hit = hit or any(box[0] - 7 < px < box[2] + 7 and box[1] - 7 < py < box[3] + 7
                             for (px, py) in dots if (px, py) != (cx, cy))
            if not hit:
                break
        else:
            lx, ly, anchor = tries[0]
        boxes.append(box)
        spots[t] = (lx, ly, anchor)
    for t in teams:
        cx, cy = x(o[t]), y(d[t])
        lx, ly, anchor = spots[t]
        light, dark = data.TEAM_COLOURS.get(t, data.DEFAULT_COLOURS)
        rk = info[t]["rk"]
        svg.append(f'<a href="{root}teams/{info[t]["slug"]}/"><g class="tm" style="--c:{light};--cd:{dark}">'
                   f'<title>{t}: AdjO {o[t]:.1f} ({rk["o"]}), AdjD {d[t]:.1f} ({rk["d"]}), {info[t]["net"]:+.1f} overall</title>'
                   f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="7"/>'
                   f'<text x="{lx:.1f}" y="{ly:.1f}" class="lab" text-anchor="{anchor}">{info[t]["code"]}</text></g></a>')
    svg.append("</svg>")
    return "".join(svg)

