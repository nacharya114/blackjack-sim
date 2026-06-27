#!/usr/bin/env python3
"""Bet-spread search: find the lowest-risk breakeven spread for a card counter.

A *bet spread* is how a Hi-Lo counter ramps wagers with the true count. Here a
spread is parametrised by a single number, its **top bet** N: you flat-bet 1
unit through true counts <= +1 and ramp linearly up to N units at true count
>= TOP_TC (default +5) -- the classic "1-to-N" spread. Wider spreads earn more
but swing harder.

The script sweeps N, runs a Monte-Carlo simulation for each, and reports:
  * total house edge (negative = player advantage); where it crosses zero is the
    *breakeven* spread,
  * per-round volatility (std/round) -- the "risk",
  * hourly EV in dollars, and
  * N0 = (std/EV)^2, the number of rounds for cumulative edge to equal one
    standard deviation (a lower N0 = a more risk-efficient, less ruin-prone
    spread; at breakeven EV->0 so N0->infinity).

The *lowest-risk breakeven spread* is the smallest N whose house edge reaches
zero: it is the least-volatile spread that stops losing money. (Exact breakeven
still has ~100% long-run risk of ruin -- to actually protect a bankroll you want
a hair more than this and a real edge; see docs/bet_spreads.md.)

Examples
--------
    python betspread.py --pen 0.75 --rounds 12000000 --cores 0
    python betspread.py --pen 0.833 --tops 1 2 3 4 5 6 --out results/betspread_deep.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

from blackjack import Rules, run_simulation

TOP_TC = 5  # true count at which the bet ramp tops out


def build_ramp(top: int, top_tc: int = TOP_TC) -> dict[int, int]:
    """A 1-to-`top` spread: 1 unit at TC<=1, linear to `top` units at TC>=top_tc."""
    ramp = {}
    for tc in range(1, top_tc + 1):
        frac = (tc - 1) / (top_tc - 1) if top_tc > 1 else 1.0
        ramp[tc] = max(1, round(1 + (top - 1) * frac))
    return ramp


def run_spread(rules: Rules, top: int, rounds: int, cores: int,
               dollars_per_unit: float, seed: int | None, engine: str = "auto") -> dict:
    ramp = build_ramp(top)
    res = run_simulation(rules, "counter", rounds=rounds,
                         strategy_kwargs={"bet_ramp": ramp, "min_bet": 1.0},
                         cores=cores, dollars_per_unit=dollars_per_unit, seed=seed,
                         engine=engine)
    t, h = res["total"], res["hourly"]
    ev, sd = t["ev_per_round_units"], t["std_per_round_units"]
    return {
        "top": top,
        "ramp": ramp,
        "total_house_edge": t["house_edge"],
        "ev_per_round_units": ev,
        "std_per_round_units": sd,
        "ci95_round_units": h["ci95_round_units"],
        "hourly_ev_dollars": h["ev_dollars"],
        "hourly_std_dollars": h["std_dollars"],
        "n0_rounds": (sd / ev) ** 2 if ev > 0 else None,
    }


def interpolate_breakeven(rows: list[dict]) -> float | None:
    """Linear-interpolate the top bet at which house edge crosses zero."""
    rows = sorted(rows, key=lambda r: r["top"])
    for a, b in zip(rows, rows[1:]):
        ha, hb = a["total_house_edge"], b["total_house_edge"]
        if ha > 0 >= hb:  # crosses from house-favoured to player-favoured
            return a["top"] + (a["top"] - b["top"]) * (-ha) / (hb - ha) * -1 \
                if (hb - ha) else a["top"]
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=12_000_000)
    ap.add_argument("--cores", type=int, default=0, help="0 = all logical cores")
    ap.add_argument("--pen", type=float, default=0.75, help="shoe penetration")
    ap.add_argument("--decks", type=int, default=6)
    ap.add_argument("--h17", action="store_true", help="dealer hits soft 17 (default S17)")
    ap.add_argument("--no-das", dest="das", action="store_false")
    ap.add_argument("--ls", action="store_true", help="late surrender on (default off)")
    ap.add_argument("--payout", type=float, default=1.5)
    ap.add_argument("--dollars-per-unit", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--tops", type=int, nargs="*", default=[1, 2, 3, 4, 5, 6, 8])
    ap.add_argument("--engine", choices=["auto", "python", "c"], default="auto",
                    help="round engine (default auto: native if built)")
    ap.add_argument("--out", help="write the full results JSON here")
    a = ap.parse_args()

    cores = (os.cpu_count() or 1) if a.cores <= 0 else a.cores
    rules = Rules(decks=a.decks, dealer_hits_soft_17=a.h17, double_after_split=a.das,
                  late_surrender=a.ls, blackjack_payout=a.payout,
                  penetration=a.pen, hands_per_hour=100)

    print(f"\n  {rules.name}   penetration={a.pen}   "
          f"rounds={a.rounds:,}   cores={cores}")
    print(f"  bet ramp tops out at true count +{TOP_TC};  "
          f"+HE = house edge, -HE = player edge\n")
    print(f"  {'spread':>8} {'ramp TC1..5':>14} {'totHE%':>9} {'95%CI':>7} "
          f"{'EV/rd':>9} {'std/rd':>7} {'$/100hr':>9} {'N0 rounds':>11}")
    rows = []
    t0 = time.time()
    for top in a.tops:
        r = run_spread(rules, top, a.rounds, cores, a.dollars_per_unit, a.seed, a.engine)
        rampstr = ",".join(str(r["ramp"][k]) for k in sorted(r["ramp"]))
        n0 = r["n0_rounds"]
        print(f"  1-{top:<6d} {rampstr:>14} {r['total_house_edge']*100:+9.4f} "
              f"{r['ci95_round_units']*100:7.4f} {r['ev_per_round_units']:+9.5f} "
              f"{r['std_per_round_units']:7.3f} {r['hourly_ev_dollars']:+9.2f} "
              f"{('%11.0f' % n0) if n0 else '        inf'}")
        rows.append(r)

    be = interpolate_breakeven(rows)
    if be is not None:
        print(f"\n  Breakeven spread ~ 1-to-{be:.1f} (house edge crosses zero here);"
              f"\n  the smallest tabulated spread that is >= breakeven is the "
              f"lowest-risk one.")
    else:
        print("\n  No zero-crossing in this range -- widen --tops.")

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        payload = {
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "rules_name": rules.name, "penetration": a.pen,
            "rounds_each": a.rounds, "top_tc": TOP_TC,
            "dollars_per_unit": a.dollars_per_unit,
            "breakeven_top": be, "results": rows,
        }
        with open(a.out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  Wrote {a.out}")
    print(f"  ({time.time() - t0:.0f}s)\n")


if __name__ == "__main__":
    main()
