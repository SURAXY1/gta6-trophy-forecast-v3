# gta6-trophy-forecast-v3

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/SURAXY1/gta6-trophy-forecast-v3/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/SURAXY1/gta6-trophy-forecast-v3/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Five-quantile forecasts of every GTA VI (PS5) trophy's Exophase rarity. One
file, `main.py`, Python standard library only, no network, no randomness.

## What it models

Exophase prints *earners / players tracked*. The first read is a small early
crowd whose earner counts barely move afterwards; each refresh then adds
thousands of players who begin with nothing. So the percentage is rebuilt from
two groups rather than extrapolated directly:

```
rate = 100 * (veterans + share * newcomers) / tracked
```

Printed percentages are turned back into whole earner counts, the veterans are
subtracted, and what is left is each trophy's share among newcomers. The clock
is `ln(1 + hours since 2026-11-19 00:00 UTC)`.

## Frozen page (every read identical)

The answer lies behind the page's first refresh, where tracked players jump by
3868/638 while the veterans stay. For each trophy the number of *new* earners
is a gamma-mixed Poisson count whose mean is keyed on the description (the
fishing challenge, chapter 1, an arcade game and a few other things a player
can do in the first hours); anything else gets about half an earner. Quantiles
are read off the count distribution and converted to the two-decimal values
the page can actually print, so a correct guess costs nothing and the band
always reaches the next printable step or two.

## Moving page (at least one refresh seen)

1. Identical reads collapse into refresh points.
2. The crowd: least squares of `ln(newcomers)` on the clock, carried forward
   at 90%.
3. Each trophy, two candidate paths for the logit of its newcomer share:
   - a **damped straight line** fitted with weights that favour recent points;
   - a **ceiling curve**, `share = top * logistic(line)`, with `top` searched
     over multiples of the share reached so far. A low ceiling pays a small
     tax, so a trophy that is still climbing is not capped by accident.
4. Trophies with few fresh earners borrow the crowd's median slope and
   steepness, in proportion to how little they have.
5. **Rehearsal.** The record is cut short at earlier refreshes and the model
   forecasts reads it already holds, over a similar reach. That picks the
   damping and the line/curve mix (pulled only halfway from a curve-leaning
   default, because a rehearsal never sees late saturation), then a median
   bias and a 90% band width for each third of the trophies ranked by share.
6. Widths get a floor that grows with the reach, a safety factor, a counting
   noise term `1/(expected earners + 1)`, and outer quantiles at 1.90 sigma
   instead of 1.645.

Outputs are rounded to 0.01, clamped to [0.01, 99.99] and forced to be
non-decreasing. If a case cannot be parsed or modelled, a wide valid answer is
still written.

## Numbers, and what they are not

Scored with my own local bench using the bounty's loss (twice the pinball loss
on the logit scale). The "answers" are stand-ins: a reconstruction of launch
night's first refresh, and simulated week-one histories cut at +59.5 h with
the target at +168 h. They are not the server's answers; the real score will
differ, and the practice stand-in in particular rewards guessing the same
opening trophies the bench assumed.

| set | persistence | previous version | this version | 90% coverage |
| --- | --- | --- | --- | --- |
| practice stand-in (2 cases) | 1.5724 | 0.0418 | 0.0377 | 0.98 |
| simulated week (12 cases) | 0.8627 | 0.3250 | 0.2523 | 0.97 |
| simulated week, 24 unseen seeds | – | 0.3178 | 0.2550 | 0.97 |

Also checked: byte-identical output on repeat runs; empty history, a single
read, missing rates, a history with no early crowd, unparseable input; a
400-refresh history finishes in under a second.

## Run

```
MAKEFUN_INPUT_DIR=./input MAKEFUN_OUTPUT_DIR=./output python3 main.py
```

Each `<id>.in` produces `<id>.out`:
`{"trophies":[{"id":n,"q":[q05,q25,q50,q75,q95]},...]}`.

MIT licensed.

<sub>Entered on [make.fun](https://make.fun/?ref=7kc96nrz&utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
