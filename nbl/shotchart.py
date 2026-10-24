"""Half-court shot charts for team pages, drawn as SVG at build time.

Shot positions are only logged from 2025-26. The feed gives x and y as percentages of a 28 x 15 m
court, with each team's shots at either end, so shots at the far end are turned round 180 degrees
to put everything on one half. Tip-ins are all logged at the same spot under the basket, which is
where they happen anyway.

Each hexagon shows how many shots came from that spot (its size). Its colour is the made % for the
whole zone it sits in against the league average for that zone, since single spots have too few
shots to say anything.
"""
import math

import numpy as np
import pandas as pd

LENGTH, WIDTH = 28.0, 15.0
BASKET = 1.575      # metres from the baseline to the centre of the basket
DEPTH = 10.5        # how much of the half court to draw
HEX = 0.42          # hexagon radius in metres
PX = 26             # pixels per metre in the SVG's own units
MIN_ZONE = 15       # fewer shots than this in a zone and it isn't coloured
FULL = 0.012        # share of a team's shots at which a hexagon is drawn full size
SCALE = 0.10        # made % this far from the league average gets the end colour

ZONES = [("rim", "At the rim"), ("short", "Short"), ("midL", "Mid-range left"), ("midC", "Mid-range middle"),
         ("midR", "Mid-range right"), ("c3L", "Corner 3 left"), ("c3R", "Corner 3 right"),
         ("a3L", "3 left wing"), ("a3C", "3 top of the key"), ("a3R", "3 right wing")]
# Where each zone's label sits on the court, in metres (across, out from the baseline).
LABELS = {"rim": (7.5, 1.0), "short": (7.5, 3.4), "midL": (2.9, 2.6), "midC": (7.5, 5.3), "midR": (12.1, 2.6),
          "c3L": (1.0, 0.9), "c3R": (14.0, 0.9), "a3L": (1.9, 7.6), "a3C": (7.5, 9.5), "a3R": (13.1, 7.6)}
# Much worse, worse, about average, better, much better.
COLOURS = ["#c0392b", "#e8822a", "#f2cf3a", "#9ccc4e", "#2e9e4f"]


def half_court(x, y):
    """Feed position -> (across, out) in metres on one half court, looking from half way at the basket."""
    if x != x or y != y:
        return None, None
    along, across = x / 100 * LENGTH, y / 100 * WIDTH
    if along > LENGTH / 2:
        return WIDTH - across, LENGTH - along
    return across, along


def zone(u, v, three):
    """One of ZONES for a shot at (u, v). Mid-range and above-the-break 3s are split by angle."""
    if u is None:
        return None
    angle = math.degrees(math.atan2(u - WIDTH / 2, v - BASKET))
    if three:
        if v < 3.0:
            return "c3L" if u < WIDTH / 2 else "c3R"
        return "a3L" if angle < -30 else ("a3R" if angle > 30 else "a3C")
    dist = math.hypot(u - WIDTH / 2, v - BASKET)
    if dist < 1.5:
        return "rim"
    if dist < 3.0:
        return "short"
    return "midL" if angle < -45 else ("midR" if angle > 45 else "midC")


def colour(diff):
    """Fill for a zone whose made % is `diff` above the league average (negative = below)."""
    t = (max(-1.0, min(1.0, diff / SCALE)) + 1) * 2
    i = min(3, int(t))
    a, b = COLOURS[i], COLOURS[i + 1]
    mix = [int(a[k:k + 2], 16) + (int(b[k:k + 2], 16) - int(a[k:k + 2], 16)) * (t - i) for k in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(c)) for c in mix)


def _hex_path(cx, cy, r):
    pts = [(cx + r * math.cos(math.radians(60 * i + 30)), cy + r * math.sin(math.radians(60 * i + 30))) for i in range(6)]
    return "M" + " L".join(f"{a * PX:.1f},{b * PX:.1f}" for a, b in pts) + "Z"


def _court():
    ends = BASKET + math.sqrt(6.75 ** 2 - 6.6 ** 2)  # where the 3-point arc meets the straight corner lines
    p = lambda m: f"{m * PX:.1f}"  # noqa: E731
    return (f'<g class="court"><rect x="0" y="0" width="{p(WIDTH)}" height="{p(DEPTH)}"/>'
            f'<rect x="{p(WIDTH / 2 - 2.45)}" y="0" width="{p(4.9)}" height="{p(5.8)}"/>'
            f'<circle cx="{p(WIDTH / 2)}" cy="{p(5.8)}" r="{p(1.8)}"/>'
            f'<path d="M{p(0.9)},0 L{p(0.9)},{p(ends)} A{p(6.75)},{p(6.75)} 0 0 0 {p(WIDTH - 0.9)},{p(ends)} L{p(WIDTH - 0.9)},0"/>'
            f'<line class="board" x1="{p(6.6)}" y1="{p(1.2)}" x2="{p(8.4)}" y2="{p(1.2)}"/>'
            f'<circle class="board" cx="{p(WIDTH / 2)}" cy="{p(BASKET)}" r="{p(0.225)}"/></g>')


def league_averages(shots):
    """Made % for each zone across every shot in the league this season."""
    return shots.dropna(subset=["zone10"]).groupby("zone10").made.mean().to_dict()


def svg(shots, league, good=1, title=""):
    """The chart for one set of shots. `good` is 1 when made shots are good for the team (its own
    shots) and -1 when they're bad (shots it allowed), so green always means good for the team."""
    sh = shots.dropna(subset=["zone10"])
    sh = sh[sh.v < DEPTH]
    if sh.empty:
        return None
    zones = sh.groupby("zone10").made.agg(["size", "mean"])
    dx, dy = math.sqrt(3) * HEX, 1.5 * HEX
    row = np.round(sh.v / dy).astype(int)
    col = np.round((sh.u - (row % 2) * dx / 2) / dx).astype(int)
    bins = (pd.DataFrame({"r": row, "c": col, "zone": sh.zone10}).groupby(["r", "c"])
            .agg(n=("zone", "size"), zone=("zone", lambda z: z.mode().iat[0])))
    parts = [f'<svg class="shotchart" viewBox="-2 -2 {WIDTH * PX + 4:.0f} {DEPTH * PX + 4:.0f}" role="img" aria-label="{title}">',
             _court()]
    for (r, c), b in bins.iterrows():
        size = HEX * 0.98 * max(0.3, math.sqrt(min(1.0, b.n / len(sh) / FULL)))
        n, made = zones.loc[b.zone, "size"], zones.loc[b.zone, "mean"]
        fill = colour((made - league.get(b.zone, made)) * good) if n >= MIN_ZONE else None
        path = _hex_path(c * dx + (r % 2) * dx / 2, r * dy, size)
        parts.append(f'<path d="{path}" fill="{fill}"/>' if fill else f'<path class="few" d="{path}"/>')
    for key, (u, v) in LABELS.items():
        if key not in zones.index:
            continue
        n, made = int(zones.loc[key, "size"]), zones.loc[key, "mean"]
        big, small = (f"{made:.0%}", f"avg {league.get(key, made):.0%}") if n >= MIN_ZONE else ("–", f"{n} shots")
        parts.append(f'<g class="zl" transform="translate({u * PX:.1f},{v * PX:.1f})"><rect x="-21" y="-12" width="42" height="25" rx="4"/>'
                     f'<text y="1">{big}</text><text class="s" y="10.5">{small}</text></g>')
    parts.append("</svg>")
    return "".join(parts)
