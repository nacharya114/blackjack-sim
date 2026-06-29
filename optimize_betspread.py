#!/usr/bin/env python3
"""Bet-spread *optimizer*: search ramp shapes to maximize a counter's edge.

Where ``betspread.py`` answers "how wide must a 1-to-N linear ramp be to break
even", this tool answers the richer question: **given a realistic table maximum
(a cap on the top bet) and the rule that we don't put money out in deeply
negative counts, what ramp SHAPE is best** -- and "best" under two objectives:

  * **EV-max**   -- the spread with the highest raw EV/round (= $/hr). Under a
    hard table cap this is degenerate-ish: it slams the cap as soon as the count
    turns advantageous and bets the table minimum (or sits out) everywhere else.
    Highest edge, highest variance, highest risk of ruin / "heat".
  * **SCORE-max** -- the spread with the best risk efficiency, SCORE = (EV/sigma)^2.
    SCORE is the bankroll-growth-rate metric counters actually optimize (it is
    proportional to Kelly log-growth and to 1/N0). It is *scale-invariant*, so
    the table cap matters only through the ramp SHAPE it permits: a wider cap
    lets you concentrate more action into the highest counts, which raises SCORE.

Modeling choices that match a real card counter:
  * **Sit out deep negative counts.** Below the ``--wong`` true count (default
    -1) the bet is 0 -- the player Wongs out / doesn't play the hand. From
    ``--wong`` up through the neutral counts the player keeps the table minimum
    (1 unit) as cover, then ramps.
  * **Be more aggressive at higher counts.** The ramp shape is controlled by an
    exponent ``gamma``: gamma<1 jumps toward the cap early (closer to EV-max),
    gamma=1 is linear-in-true-count (~Kelly, since advantage is ~linear in TC),
    gamma>1 holds small bets through marginal counts and saves the big bets for
    the richest shoes (lower variance, more "back-loaded").
  * **Honor the table maximum.** Every bet is clamped to ``[1, cap]`` units, so
    ``cap`` is literally max-bet / table-minimum (a $25-$500 table is cap=20).

The search grids over (ramp_start, top_tc, gamma) for each cap, ranks coarsely
with common random numbers (one shared seed -> the strategy difference, not RNG
noise, drives the ranking), then re-simulates the EV-max and SCORE-max finalists
at high precision. A few reference spreads (flat, play-through, classic linear)
are simulated too so the optimizer's picks can be read against familiar anchors.

Caveat on Wonging: rounds the player sits out still count as rounds at the table
in EV/round and SCORE here, so both *understate* a back-counter who table-hops to
play only good shoes -- the comparison between spreads is apples-to-apples, but
the absolute $/hr is conservative for a true Wong. See docs/bet_spread_optimization.md.

Examples
--------
    python optimize_betspread.py --pen 0.75 --caps 12 20 40 \
        --rounds 12000000 --rounds-final 60000000 --cores 0 \
        --out results/betspread_opt_pen75.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from blackjack import Rules, run_simulation


def build_ramp(cap: int, ramp_start: int, top_tc: int, gamma: float,
               wong: int = -1, base: int = 1) -> dict[int, int]:
    """Construct an integer bet ramp keyed by floor(true count).

    * floor(TC) < ``wong``        -> 0 units (sit out; via min_bet=0 at sim time)
    * ``wong`` <= floor(TC) < ``ramp_start`` -> ``base`` units (table-min cover)
    * ``ramp_start`` <= floor(TC) <= ``top_tc`` -> base..cap along ``frac**gamma``
    * floor(TC) >= ``top_tc``     -> ``cap`` units (bet tops out)
    """
    ramp: dict[int, int] = {}
    for tc in range(wong, ramp_start):
        ramp[tc] = base
    span = top_tc - ramp_start
    for tc in range(ramp_start, top_tc + 1):
        frac = 1.0 if span <= 0 else ((tc - ramp_start) / span) ** gamma
        ramp[tc] = max(base, min(cap, round(base + (cap - base) * frac)))
    return ramp


def evaluate(rules: Rules, ramp: dict[int, int], min_bet: float, rounds: int,
             cores: int, dollars_per_unit: float, seed: int, engine: str) -> dict:
    """Simulate one spread and return its EV / risk metrics."""
    res = run_simulation(rules, "counter", rounds=rounds,
                         strategy_kwargs={"bet_ramp": ramp, "min_bet": min_bet},
                         cores=cores, dollars_per_unit=dollars_per_unit,
                         seed=seed, engine=engine)
    t, h, m = res["total"], res["hourly"], res["main"]
    ev, sd = t["ev_per_round_units"], t["std_per_round_units"]
    score = (ev / sd) ** 2 * 1e6 if sd > 0 and ev > 0 else 0.0
    return {
        "ramp": dict(sorted(ramp.items())),
        "min_bet": min_bet,
        "house_edge": m["house_edge"],
        "ev_per_round_units": ev,
        "std_per_round_units": sd,
        "ci95_round_units": h["ci95_round_units"],
        "hourly_ev_dollars": h["ev_dollars"],
        "hourly_std_dollars": h["std_dollars"],
        "avg_initial_units": m["avg_initial_units"],
        "n0_rounds": (sd / ev) ** 2 if ev > 0 else None,
        "score": score,           # (EV/sigma)^2 * 1e6 ; risk-efficiency, scale-invariant
        "engine": res["engine"],
        "rounds": res["rounds"],
    }


def ramp_str(ramp: dict[int, int]) -> str:
    """Compact 'TC:bet' rendering, sorted by true count."""
    return ",".join(f"{k}:{v}" for k, v in sorted(ramp.items()))


# Grid of ramp shapes searched for every cap.
GAMMAS = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)
RAMP_STARTS = (1, 2, 3)
TOP_TCS = (4, 5, 6)


def search_cap(rules: Rules, cap: int, wong: int, rounds: int, rounds_final: int,
               cores: int, dpu: float, seed: int, engine: str) -> dict:
    """Grid-search ramp shapes for one table cap; refine the two winners."""
    print(f"\n  === table cap 1-to-{cap}  (max bet = {cap} x table minimum) ===")
    coarse = []
    for ramp_start in RAMP_STARTS:
        for top_tc in TOP_TCS:
            if ramp_start >= top_tc:
                continue
            for gamma in GAMMAS:
                ramp = build_ramp(cap, ramp_start, top_tc, gamma, wong=wong)
                row = evaluate(rules, ramp, 0.0, rounds, cores, dpu, seed, engine)
                row.update(cap=cap, ramp_start=ramp_start, top_tc=top_tc, gamma=gamma)
                coarse.append(row)
    # Coarse winners under each objective.
    ev_pick = max(coarse, key=lambda r: r["ev_per_round_units"])
    score_pick = max(coarse, key=lambda r: r["score"])
    print(f"  coarse EV-max   : {ramp_str(ev_pick['ramp']):28s} "
          f"EV/rd {ev_pick['ev_per_round_units']:+.5f}  $/100hr {ev_pick['hourly_ev_dollars']:+.2f}")
    print(f"  coarse SCORE-max: {ramp_str(score_pick['ramp']):28s} "
          f"SCORE {score_pick['score']:7.1f}  N0 {(score_pick['n0_rounds'] or 0)/1e6:5.2f}M")

    # Refine: re-simulate the unique finalists at high precision.
    finalists = {}
    for tag, pick in (("ev_max", ev_pick), ("score_max", score_pick)):
        key = ramp_str(pick["ramp"])
        if key not in finalists:
            fine = evaluate(rules, pick["ramp"], 0.0, rounds_final, cores, dpu, seed, engine)
            fine.update(cap=cap, ramp_start=pick["ramp_start"],
                        top_tc=pick["top_tc"], gamma=pick["gamma"])
            finalists[key] = fine
        finalists[key].setdefault("objectives", []).append(tag)

    return {
        "cap": cap,
        "ev_max": next(v for v in finalists.values() if "ev_max" in v["objectives"]),
        "score_max": next(v for v in finalists.values() if "score_max" in v["objectives"]),
        "coarse": coarse,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=12_000_000, help="rounds per coarse grid point")
    ap.add_argument("--rounds-final", type=int, default=60_000_000, help="rounds for the finalists")
    ap.add_argument("--cores", type=int, default=0, help="0 = all logical cores")
    ap.add_argument("--pen", type=float, default=0.75, help="shoe penetration")
    ap.add_argument("--decks", type=int, default=6)
    ap.add_argument("--h17", action="store_true", help="dealer hits soft 17 (default S17)")
    ap.add_argument("--no-das", dest="das", action="store_false")
    ap.add_argument("--no-ls", dest="ls", action="store_false")
    ap.add_argument("--payout", type=float, default=1.5)
    ap.add_argument("--dollars-per-unit", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--wong", type=int, default=-1,
                    help="sit out (bet 0) when floor(true count) is below this (default -1)")
    ap.add_argument("--caps", type=int, nargs="*", default=[12, 20, 40],
                    help="table maximums to optimize within (max bet / table minimum)")
    ap.add_argument("--engine", choices=["auto", "python", "c"], default="auto")
    ap.add_argument("--out", help="write the full results JSON here")
    a = ap.parse_args()

    cores = (os.cpu_count() or 1) if a.cores <= 0 else a.cores
    rules = Rules(decks=a.decks, dealer_hits_soft_17=a.h17, double_after_split=a.das,
                  late_surrender=a.ls, blackjack_payout=a.payout,
                  penetration=a.pen, hands_per_hour=100)

    print(f"\n  {rules.name}   penetration={a.pen}   "
          f"sit out below TC {a.wong}   {cores} cores")
    print(f"  coarse={a.rounds:,} rds/point   finalists={a.rounds_final:,} rds   "
          f"common-random-numbers seed={a.seed}")

    t0 = time.time()
    caps = [search_cap(rules, c, a.wong, a.rounds, a.rounds_final, cores,
                       a.dollars_per_unit, a.seed, a.engine) for c in a.caps]

    # Reference spreads (no cap search): flat, classic play-through 1-8 linear,
    # and the same 1-8 linear but Wonging out below `--wong`.
    print("\n  === reference spreads (for comparison) ===")
    refs = []
    ref_specs = [
        # flat: ramp {0:1} with min_bet 1 -> 1 unit at every count.
        ("flat 1u", {0: 1}, 1.0),
        # play-through 1-8 linear: positive keys only, min_bet 1 -> 1 unit through
        # all neutral/negative counts (the classic "sit and grind", no Wonging).
        ("1-8 linear, play-through", build_ramp(8, 1, 5, 1.0, wong=1), 1.0),
        # same 1-8 linear but Wong out below `--wong` (min_bet 0 = bet nothing).
        ("1-8 linear, wong<%d" % a.wong, build_ramp(8, 1, 5, 1.0, wong=a.wong), 0.0),
    ]
    for name, ramp, mb in ref_specs:
        row = evaluate(rules, ramp, mb, a.rounds_final, cores, a.dollars_per_unit, a.seed, a.engine)
        row["name"] = name
        refs.append(row)
        print(f"  {name:28s} {ramp_str(ramp):24s} EV/rd {row['ev_per_round_units']:+.5f}  "
              f"SCORE {row['score']:7.1f}  $/100hr {row['hourly_ev_dollars']:+.2f}")

    # Summary table.
    print("\n  === optimized spreads by table cap ===")
    print(f"  {'cap':>4} {'objective':>9} {'ramp (TC:bet)':>30} {'EV/rd':>9} "
          f"{'std/rd':>7} {'$/100hr':>9} {'SCORE':>7} {'N0(M)':>7}")
    for c in caps:
        for tag in ("ev_max", "score_max"):
            r = c[tag]
            n0 = (r["n0_rounds"] or 0) / 1e6
            print(f"  1-{c['cap']:<2d} {tag:>9} {ramp_str(r['ramp']):>30} "
                  f"{r['ev_per_round_units']:+9.5f} {r['std_per_round_units']:7.3f} "
                  f"{r['hourly_ev_dollars']:+9.2f} {r['score']:7.1f} {n0:7.2f}")

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        payload = {
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "rules_name": rules.name, "penetration": a.pen, "decks": a.decks,
            "wong_below": a.wong, "dollars_per_unit": a.dollars_per_unit,
            "rounds_coarse": a.rounds, "rounds_final": a.rounds_final,
            "grid": {"gammas": list(GAMMAS), "ramp_starts": list(RAMP_STARTS),
                     "top_tcs": list(TOP_TCS)},
            "caps": caps, "references": refs,
        }
        with open(a.out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\n  Wrote {a.out}")
    print(f"  ({time.time() - t0:.0f}s)\n")


if __name__ == "__main__":
    main()
