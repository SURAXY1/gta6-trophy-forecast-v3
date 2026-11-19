#!/usr/bin/env python3
"""GTA VI trophy rarity forecaster, third cut.

Cohort view of an Exophase rarity page: a fixed early crowd (the veterans)
plus newcomers who arrive with almost nothing and catch up over the week.

    rate = 100 * (veterans_i + share_i * newcomers) / tracked

Two situations are handled:
  * frozen page  - every read identical; the answer sits behind the page's
                   first refresh, so integer newcomer earner counts are
                   forecast and the quantiles land on printable rates;
  * moving page  - refresh points give log-time trends for the crowd size
                   and for each trophy's newcomer share; rehearsals on a
                   shortened history set damping, bias and band widths.

Standard library only. Same input, same bytes out.
"""
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone

LAUNCH = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)
RATE_LO, RATE_HI = 0.01, 99.99
FIVE = (0.05, 0.25, 0.5, 0.75, 0.95)
# z multiples for the five levels; the outer pair is stretched a little past
# the normal 1.645 because misses beyond the tails cost far more than slack.
Z_INNER, Z_OUTER = 0.674, 1.90
FIRST_REFRESH_GROWTH = 3868.0 / 638.0


def parse_when(text):
    """ISO stamp -> hours after launch midnight (UTC)."""
    stamp = str(text).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        moment = datetime.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment - LAUNCH).total_seconds() / 3600.0


def logtime(hours):
    return math.log(1.0 + max(hours, 0.0))


def clip_rate(pct):
    return min(RATE_HI, max(RATE_LO, pct))


def to_logit(pct):
    frac = clip_rate(pct) / 100.0
    return math.log(frac / (1.0 - frac))


def from_logit(value):
    if value > 40.0:
        return RATE_HI
    if value < -40.0:
        return RATE_LO
    return clip_rate(100.0 / (1.0 + math.exp(-value)))


def frac_logit(frac):
    frac = min(1.0 - 1e-9, max(1e-9, frac))
    return math.log(frac / (1.0 - frac))


def frac_expit(value):
    if value > 40.0:
        return 1.0
    if value < -40.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def middle(values, fallback=0.0):
    seq = sorted(values)
    if not seq:
        return fallback
    half = len(seq) // 2
    return seq[half] if len(seq) % 2 else 0.5 * (seq[half - 1] + seq[half])


def quantile_of(values, level, fallback=0.0):
    seq = sorted(values)
    if not seq:
        return fallback
    spot = level * (len(seq) - 1)
    under = int(math.floor(spot))
    over = min(len(seq) - 1, under + 1)
    return seq[under] + (seq[over] - seq[under]) * (spot - under)


def tidy(quants):
    """Round to the page's 2 decimals, clamp, and force a non-decreasing run."""
    out, floor_value = [], RATE_LO
    for value in quants:
        if value != value:  # NaN guard
            value = floor_value
        value = round(clip_rate(max(value, floor_value)), 2)
        value = clip_rate(value)
        out.append(value)
        floor_value = value
    return out


def band(center, width, lower_scale=1.0, upper_scale=1.0):
    """Five quantiles around a logit-scale center."""
    lo, hi = width * lower_scale, width * upper_scale
    return [
        from_logit(center - Z_OUTER * lo),
        from_logit(center - Z_INNER * lo),
        from_logit(center),
        from_logit(center + Z_INNER * hi),
        from_logit(center + Z_OUTER * hi),
    ]


def last_resort(case):
    """A valid, very wide answer for when everything else failed."""
    rows = []
    trophies = case.get("trophies") or []
    latest = None
    for read in reversed(case.get("history") or []):
        if isinstance(read, dict) and isinstance(read.get("rates"), list):
            latest = read["rates"]
            break
    for k, trophy in enumerate(trophies):
        seen = None
        if latest is not None and k < len(latest) and isinstance(latest[k], (int, float)):
            seen = float(latest[k])
        if seen is None:
            quants = [0.05, 0.5, 3.0, 15.0, 60.0]
        elif seen >= 99.5:
            quants = [99.9, 99.98, 99.99, 99.99, 99.99]
        else:
            quants = band(to_logit(seen) - 0.4, 1.3)
        ident = trophy.get("id", k + 1) if isinstance(trophy, dict) else k + 1
        rows.append({"id": ident, "q": tidy(quants)})
    return rows


# --------------------------------------------------------------- the history
def refresh_points(case):
    """Collapse the reads into the moments the page actually changed.

    Each point: hours after launch, tracked head count, and per trophy the
    integer earner count implied by the printed percentage (None if absent).
    """
    width = len(case["trophies"])
    points, previous = [], None
    for read in case.get("history") or []:
        try:
            head = int(read["players_tracked"])
            raw = list(read["rates"])
        except (KeyError, TypeError, ValueError):
            continue
        if head <= 0 or len(raw) != width:
            continue
        rates = [float(r) if isinstance(r, (int, float)) else None for r in raw]
        signature = (head, tuple(rates))
        if signature == previous:
            continue
        previous = signature
        try:
            hours = parse_when(read.get("at") or read.get("slot"))
        except (TypeError, ValueError):
            continue
        counts = [None if r is None else int(round(r * head / 100.0)) for r in rates]
        points.append({"h": hours, "head": head, "counts": counts, "rates": rates})
    return points


def fill_gaps(points):
    """Carry the nearest known count into any hole so later maths is total."""
    width = len(points[0]["counts"])
    for k in range(width):
        known = None
        for point in points:
            if point["counts"][k] is None:
                if known is not None:
                    point["counts"][k] = min(point["head"], known)
            else:
                known = point["counts"][k]
        known = None
        for point in reversed(points):
            if point["counts"][k] is None:
                point["counts"][k] = min(point["head"], known) if known is not None else 0
            else:
                known = point["counts"][k]


# ------------------------------------------------------------- frozen page
# Share of first-refresh newcomers expected to hold a trophy, keyed on words
# in its description: what a player can plausibly do in the opening hours.
# Second number: gamma shape (bigger = I trust the guess more).
OPENING_HOURS = (
    ("fishing challenge", 0.0160, 4.0),
    ("chapter 1.", 0.0060, 3.0),
    ("arcade game", 0.0030, 2.5),
    ("breakfast run", 0.0016, 2.0),
    ("discover 100 vehicles", 0.0015, 2.0),
    ("unscheduled departure", 0.0009, 1.5),
    ("chapter 2.", 0.0006, 1.2),
    ("what the hell just happened", 0.0006, 1.2),
    ("rhinos", 0.0006, 1.2),
    ("bowling alley", 0.0004, 1.0),
    ("4 star wanted", 0.0004, 1.0),
    ("cocktail", 0.0004, 1.0),
)
# Everything else: about a coin flip's worth of one stray earner. Launch
# night showed a good third of these picked one up, so the upper quartile
# sits one earner above the veterans and the top level two above.
ANYTHING_ELSE = (0.000155, 1.0)
# Outer levels are read a touch further out than 5/95 on the count scale.
COUNT_LEVELS = (0.04, 0.25, 0.5, 0.75, 0.96)


def opening_guess(trophy):
    text = (str(trophy.get("description", "")) + " " + str(trophy.get("name", ""))).lower()
    for needle, share, shape in OPENING_HOURS:
        if needle in text:
            return share, shape
    return ANYTHING_ELSE


def count_quantiles(mean, shape, levels):
    """Quantiles of a gamma-mixed Poisson count with the given mean."""
    if mean <= 0.0:
        return [0 for _ in levels]
    stay = shape / (shape + mean)
    move = 1.0 - stay
    mass = stay ** shape
    total, k, answers, todo = 0.0, 0, [], list(levels)
    while todo and k < 200000:
        total += mass
        while todo and total >= todo[0] - 1e-12:
            answers.append(k)
            todo.pop(0)
        mass *= (k + shape) / (k + 1.0) * move
        k += 1
    answers.extend([k] * len(todo))
    return answers


def frozen_page(case, points):
    """Every read is the same: forecast the page's first refresh."""
    only = points[-1]
    head_now = only["head"]
    head_next = max(head_now + 1, int(round(head_now * FIRST_REFRESH_GROWTH)))
    arrivals = head_next - head_now
    rows = []
    for k, trophy in enumerate(case["trophies"]):
        veterans = only["counts"][k]
        if veterans >= 0.995 * head_now:
            # held by everyone tracked: tracking seems to start with it
            rows.append({"id": trophy["id"], "q": tidy([99.97, 99.99, 99.99, 99.99, 99.99])})
            continue
        share, shape = opening_guess(trophy)
        extra = count_quantiles(share * arrivals, shape, COUNT_LEVELS)
        quants = [100.0 * (veterans + more) / head_next for more in extra]
        rows.append({"id": trophy["id"], "q": tidy(quants)})
    return rows


# -------------------------------------------------------------- moving page
RECENCY = 0.7          # e-folding length, in log-time, of the line's weights
LONG_MEMORY = 1.0      # same for the ceiling curve, which needs the bend
CROWD_PULL = 30.0      # fresh earners at which a trophy's own slope counts half
MOST_POINTS = 60       # a very busy record is thinned to this many refreshes
LEVEL_PULL = 12.0      # same idea for trusting its latest observed share
GROWTH_DAMP = 0.9      # the crowd's own growth is carried forward almost fully
SLOPE_RANGE = (-0.3, 4.0)
DEFAULT_SLOPE = 1.0
DEFAULT_GROWTH = 0.9
# candidate ceilings, as multiples of the share a trophy has reached so far
CEILINGS = (1.04, 1.1, 1.2, 1.35, 1.6, 2.0, 3.0, 5.0, 10.0, 30.0, 100.0, 1000.0)
CEILING_CAP = 0.98
LOW_CEILING_TAX = 0.03  # a low ceiling must earn its place with a better fit


def weighted_line(xs, ys, ws):
    """Weighted least squares; returns (slope, value at the last x) or None."""
    total = sum(ws)
    if len(xs) < 2 or total <= 0.0:
        return None
    mx = sum(w * x for w, x in zip(ws, xs)) / total
    my = sum(w * y for w, y in zip(ws, ys)) / total
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if sxx < 1e-9:
        return None
    sxy = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys))
    slope = sxy / sxx
    return slope, my + slope * (xs[-1] - mx)


def ceiling_curve(xs, fracs, ws):
    """share = top * logistic(line in log-time); the top is searched on a grid.

    Returns (top, steepness, line value at the last x) or None.
    """
    if len(xs) < 4:
        return None
    ys = [frac_logit(f) for f in fracs]
    peak, x_end, weight_sum = max(fracs), xs[-1], sum(ws)
    best, best_cost, tried = None, None, set()
    for multiple in CEILINGS:
        top = min(CEILING_CAP, fracs[-1] * multiple)
        if top <= peak * 1.01 or top in tried:
            continue
        tried.add(top)
        fit = weighted_line(xs, [frac_logit(f / top) for f in fracs], ws)
        if fit is None:
            continue
        steep, at_end = max(0.0, fit[0]), fit[1]
        miss = 0.0
        for x, y, w in zip(xs, ys, ws):
            modelled = frac_logit(top * frac_expit(at_end + steep * (x - x_end)))
            miss += w * (y - modelled) ** 2
        cost = (miss / weight_sum + 1e-6) * (1.0 + LOW_CEILING_TAX * math.log(CEILING_CAP / top))
        if best_cost is None or cost < best_cost:
            best, best_cost = (top, steep, at_end), cost
    return best


def study(points, x_target):
    """Fit the crowd and every trophy on the given refresh points.

    Returns the pieces a forecast is assembled from, so that the rehearsal
    can try several ways of assembling them without refitting.
    """
    first = points[0]
    base_head = first["head"]
    live = [p for p in points if p["head"] > base_head]
    if len(live) > MOST_POINTS:
        # keep the newest and an even spread of the rest
        stride = len(live) / float(MOST_POINTS)
        keep = sorted({len(live) - 1 - int(j * stride) for j in range(MOST_POINTS)})
        live = [live[j] for j in keep]
    latest = live[-1]
    xs = [logtime(p["h"]) for p in live]
    x_now = xs[-1]
    reach = max(0.0, x_target - x_now)
    near = [math.exp(-(x_now - x) / RECENCY) for x in xs]
    far = [math.exp(-(x_now - x) / LONG_MEMORY) for x in xs]
    fresh_heads = [p["head"] - base_head for p in live]

    fit = weighted_line(xs, [math.log(m) for m in fresh_heads], near)
    growth = DEFAULT_GROWTH if fit is None else min(2.0, max(0.1, fit[0]))
    crowd_then = fresh_heads[-1] * math.exp(GROWTH_DAMP * growth * reach)

    items = []
    for k in range(len(first["counts"])):
        veterans = first["counts"][k]
        if veterans >= 0.995 * base_head and latest["counts"][k] >= 0.995 * latest["head"]:
            items.append(None)
            continue
        got = [max(0, p["counts"][k] - veterans) for p in live]
        fracs = [(g + 0.5) / (m + 1.0) for g, m in zip(got, fresh_heads)]
        ys = [frac_logit(f) for f in fracs]
        line = weighted_line(xs, ys, near)
        trust = got[-1] / (got[-1] + LEVEL_PULL)
        level = ys[-1] if line is None else trust * ys[-1] + (1.0 - trust) * line[1]
        # points with almost no earners say little about the bend
        told = [w * (g + 0.5) / (g + 3.5) for w, g in zip(far, got)]
        items.append({
            "veterans": veterans,
            "earners": got[-1],
            "level": level,
            "slope": None if line is None else line[0],
            "curve": ceiling_curve(xs, fracs, told),
        })

    def crowd_value(pick, fallback):
        solid = [pick(it) for it in items if it and pick(it) is not None and it["earners"] >= 25]
        if len(solid) < 3:
            solid = [pick(it) for it in items if it and pick(it) is not None]
        return middle(solid, fallback)

    crowd_slope = crowd_value(lambda it: it["slope"], DEFAULT_SLOPE)
    crowd_steep = crowd_value(lambda it: it["curve"][1] if it["curve"] else None, crowd_slope)

    for it in items:
        if it is None:
            continue
        lean = it["earners"] / (it["earners"] + CROWD_PULL)
        own = crowd_slope if it["slope"] is None else it["slope"]
        slope = lean * own + (1.0 - lean) * crowd_slope
        it["slope"] = min(SLOPE_RANGE[1], max(SLOPE_RANGE[0], slope))
        if it["curve"] is None:
            # too little record for a bend: a straight run at the crowd's pace
            it["bent"] = it["level"] + crowd_steep * reach
        else:
            top, steep, at_end = it["curve"]
            steep = lean * steep + (1.0 - lean) * crowd_steep
            # start from the same smoothed level the line uses
            start = frac_logit(min(0.999, frac_expit(it["level"]) / top))
            start = lean * start + (1.0 - lean) * at_end
            it["bent"] = frac_logit(top * frac_expit(start + steep * reach))
    return {"items": items, "crowd": crowd_then, "head": base_head + crowd_then, "reach": reach}


def assemble(fitted, damp, mix):
    """Blend the damped line with the ceiling curve and rebuild the rates.

    Per trophy: None, or (logit of rate, expected earners, newcomer share now).
    """
    out = []
    for it in fitted["items"]:
        if it is None:
            out.append(None)
            continue
        straight = it["level"] + damp * it["slope"] * fitted["reach"]
        share_then = frac_expit(mix * it["bent"] + (1.0 - mix) * straight)
        count_then = it["veterans"] + share_then * fitted["crowd"]
        out.append((to_logit(100.0 * count_then / fitted["head"]), count_then, frac_expit(it["level"])))
    return out


# ---------------------------------------------------------------- rehearsal
DAMP_GRID = (0.45, 0.6, 0.75, 0.9)
MIX_GRID = (0.5, 0.75, 1.0)
# Damping and curve weight with nothing to rehearse on. The curve gets most
# of the say: a rehearsal only ever sees the early, unsaturated stretch of
# the record, so it underrates how hard the common trophies flatten later.
HOME = (0.7, 0.85)
TRUST = 0.5            # how far the rehearsed choice may pull away from home
BIAS_KEEP = 0.6        # fraction of a group's rehearsed median miss removed
WIDTH_SAFETY = 1.15
GROUPS = 3


def width_floor(reach):
    return 0.16 + 0.14 * reach


def width_default(reach):
    return 0.30 + 0.45 * reach


def group_of(shares):
    """Rank the trophies by newcomer share and cut the ranking into bands."""
    order = sorted((s, k) for k, s in enumerate(shares) if s is not None)
    tags = [None] * len(shares)
    for spot, (_, k) in enumerate(order):
        tags[k] = min(GROUPS - 1, spot * GROUPS // max(1, len(order)))
    return tags


def rehearse(points, reach):
    """Forecast reads we already hold from a shortened history.

    Returns (damping, curve weight, per-group bias, per-group width) for a
    forecast whose log-time reach is `reach`; the last two are None when
    the record is too short to rehearse.
    """
    base_head = points[0]["head"]
    live_at = [i for i, p in enumerate(points) if p["head"] > base_head]
    trials = []
    for goal in live_at[-3:]:
        x_goal = logtime(points[goal]["h"])
        for order, cut in enumerate(live_at):
            if cut >= goal or order < 3:
                continue
            gap = x_goal - logtime(points[cut]["h"])
            if gap < 0.3 * reach or gap > 1.4 * reach:
                continue
            trials.append((cut, goal, gap))
    if not trials:
        return HOME[0], HOME[1], None, None
    # thin the list so one stretch of the record cannot dominate
    trials = trials[:: max(1, len(trials) // 14)]
    fits = []
    for cut, goal, gap in trials:
        target = points[goal]
        actual = [to_logit(100.0 * c / target["head"]) for c in target["counts"]]
        fits.append((study(points[: cut + 1], logtime(target["h"])), actual, reach / gap))

    def misses(damp, mix):
        rows = []
        for fitted, actual, stretch in fits:
            guess = assemble(fitted, damp, mix)
            tags = group_of([g[2] if g else None for g in guess])
            for k, g in enumerate(guess):
                if g is not None:
                    rows.append((tags[k], actual[k] - g[0], stretch))
        return rows

    best, best_cost = HOME, None
    for damp in DAMP_GRID:
        for mix in MIX_GRID:
            rows = misses(damp, mix)
            cost = sum(abs(err) / max(1.0, stretch) for _, err, stretch in rows) / max(1, len(rows))
            if best_cost is None or cost < best_cost - 1e-12:
                best, best_cost = (damp, mix), cost
    damp = HOME[0] + TRUST * (best[0] - HOME[0])
    mix = HOME[1] + TRUST * (best[1] - HOME[1])

    rows = misses(damp, mix)
    bias, width = [], []
    for tag in range(GROUPS):
        mine = [(err, min(1.5, stretch)) for t, err, stretch in rows if t == tag]
        if len(mine) < 6:
            bias.append(0.0)
            width.append(None)
            continue
        shift = BIAS_KEEP * middle([err * stretch for err, stretch in mine])
        spread = [abs(err * stretch - shift) * max(0.7, stretch) ** -0.3 for err, stretch in mine]
        bias.append(shift)
        width.append(WIDTH_SAFETY * quantile_of(spread, 0.9) / 1.645)
    return damp, mix, bias, width


def moving_page(case, points):
    x_target = logtime(parse_when(case["target"]))
    reach = max(0.0, x_target - logtime(points[-1]["h"]))
    damp, mix, bias, width = rehearse(points, reach)
    guess = assemble(study(points, x_target), damp, mix)
    tags = group_of([g[2] if g else None for g in guess])
    known = [w for w in (width or []) if w is not None]
    rows = []
    for k, trophy in enumerate(case["trophies"]):
        g = guess[k]
        if g is None:
            rows.append({"id": trophy["id"], "q": tidy([99.98, 99.99, 99.99, 99.99, 99.99])})
            continue
        center, count_then, _ = g
        if bias is None:
            spread = width_default(reach)
        else:
            center += bias[tags[k]]
            spread = width[tags[k]]
            if spread is None:
                spread = max(known) if known else width_default(reach)
            spread = max(spread, width_floor(reach))
        # counting noise on top of the trend noise
        spread = math.sqrt(spread * spread + 1.0 / (count_then + 1.0))
        rows.append({"id": trophy["id"], "q": tidy(band(center, spread))})
    return rows


# --------------------------------------------------------------------- main
def forecast(case):
    points = refresh_points(case)
    if not points:
        return last_resort(case)
    fill_gaps(points)
    if all(p["head"] <= points[0]["head"] for p in points):
        return frozen_page(case, points[-1:])
    return moving_page(case, points)


def main():
    src = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    dst = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(dst, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(src, "*.in"))):
        name = os.path.splitext(os.path.basename(path))[0]
        case = {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                case = json.load(handle)
            rows = forecast(case)
        except Exception:  # never leave a case unanswered
            try:
                rows = last_resort(case if isinstance(case, dict) else {})
            except Exception:
                rows = []
        with open(os.path.join(dst, name + ".out"), "w", encoding="utf-8") as handle:
            json.dump({"trophies": rows}, handle, separators=(",", ":"))


if __name__ == "__main__":
    main()
