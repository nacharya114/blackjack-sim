#!/usr/bin/env python3
"""Can you beat a *windowed* CSM by counting? (after discountgambling.net)

A real continuous shuffle machine (e.g. a one2six) holds a buffer of recently
played cards out of the dealing pool. That leaves a small, short-lived signal:
the Hi-Lo count of the buffer tells you how ten-rich the pool is right now. This
script reproduces that analysis on our engine:

  1. deal a windowed CSM flat and bin each round's result by the buffer count at
     the start of the round -> the per-count EV curve + how often each count
     occurs (the post's headline: the count is >= +5 about 8.2% of the time and
     those hands are slightly +EV);
  2. run a `window_counter` that bets up when the buffer count is high and report
     its edge per unit wagered, for a few bet spreads.

    python csm_counting.py --rounds 200000000 --buffer 16 --out results/csm_counting.json

Model assumptions follow the post: 6 decks, a 16-card buffer, a plain Hi-Lo count
of the buffer used directly (no true-count divisor), bet up at +5.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict

from blackjack import run_simulation
from blackjack.game import play_round
from blackjack.players import BasicStrategy
from blackjack.rules import Rules
from blackjack.shoe import Shoe


def _bin_chunk(args):
    rules_dict, buffer, decks, n, warmup, seed = args
    rules = Rules.from_dict(rules_dict)
    rng = random.Random(seed)
    shoe = Shoe(decks, 0.0, rng=rng, csm_buffer=buffer)
    strat = BasicStrategy()
    counts = defaultdict(int)
    nets = defaultdict(float)
    for i in range(n):
        wc = shoe.window_count()
        r = play_round(shoe, rules, strat)
        if i < warmup:
            continue
        counts[wc] += 1
        nets[wc] += r.main_net
    return dict(counts), dict(nets)


def bin_by_window_count(rules: Rules, buffer: int, rounds: int, cores: int,
                        seed: int) -> dict:
    """Flat-bet the windowed CSM; return per-buffer-count frequency and EV."""
    warmup = 2000
    counts: dict = defaultdict(int)
    nets: dict = defaultdict(float)
    if cores == 1:
        c, ns = _bin_chunk((rules.to_dict(), buffer, rules.decks, rounds, warmup, seed))
        for k, v in c.items():
            counts[k] += v
        for k, v in ns.items():
            nets[k] += v
    else:
        import multiprocessing as mp
        per = rounds // cores
        jobs = [(rules.to_dict(), buffer, rules.decks, per, warmup, seed + i)
                for i in range(cores)]
        with mp.Pool(cores) as pool:
            for c, ns in pool.map(_bin_chunk, jobs):
                for k, v in c.items():
                    counts[k] += v
                for k, v in ns.items():
                    nets[k] += v
    total = sum(counts.values())
    rows = []
    for wc in sorted(counts):
        n = counts[wc]
        rows.append({"wc": wc, "freq": n / total, "ev": nets[wc] / n, "n": n})
    return {"total": total, "rows": rows}


def run_counter(rules: Rules, ramp: dict, min_bet: float, rounds: int, cores: int,
                seed: int) -> dict:
    # engine="auto": the native engine now supports the windowed shoe.
    res = run_simulation(rules, "window_counter", rounds=rounds, cores=cores, seed=seed,
                         engine="auto",
                         strategy_kwargs={"bet_ramp": ramp, "min_bet": min_bet})
    m = res["main"]
    return {"ramp": ramp, "min_bet": min_bet, "house_edge": m["house_edge"],
            "element_of_risk": m["element_of_risk"],
            "avg_wager_units": m["avg_wager_units"],
            "std_per_round_units": res["total"]["std_per_round_units"],
            "engine": res.get("engine")}


# name -> (bet ramp keyed on the buffer count, min bet below the ramp).
# min_bet 0 = "Wong" (sit the hand out entirely).
SPREADS = {
    "spread 1-12 @ +5": ({5: 4, 6: 6, 7: 8, 8: 10, 9: 12}, 1.0),
    "spread 1-20 @ +5": ({5: 8, 6: 12, 7: 16, 8: 20, 9: 20}, 1.0),
    "Wong play >= +5": ({5: 1, 6: 1, 7: 1, 8: 1, 9: 1}, 0.0),
    "Wong+spread >= +5": ({5: 1, 6: 2, 7: 3, 8: 5, 9: 8}, 0.0),
    "Wong play >= +8": ({8: 1, 9: 1, 10: 1, 11: 1, 12: 1}, 0.0),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=100_000_000)
    ap.add_argument("--buffer", type=int, default=16, help="CSM buffer depth (cards)")
    ap.add_argument("--decks", type=int, default=6)
    ap.add_argument("--h17", action="store_true")
    ap.add_argument("--no-das", dest="das", action="store_false")
    ap.add_argument("--no-ls", dest="ls", action="store_false")
    ap.add_argument("--payout", type=float, default=1.5)
    ap.add_argument("--cores", type=int, default=0)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--out", help="write the full JSON report here")
    a = ap.parse_args()

    cores = (os.cpu_count() or 1) if a.cores <= 0 else a.cores
    rules = Rules(decks=a.decks, dealer_hits_soft_17=a.h17, double_after_split=a.das,
                  late_surrender=a.ls, blackjack_payout=a.payout, csm_buffer=a.buffer)

    print(f"\n  {rules.name}   buffer={a.buffer} cards   rounds={a.rounds:,}   cores={cores}\n")
    t0 = time.time()

    binned = bin_by_window_count(rules, a.buffer, a.rounds, cores, a.seed)
    ge5 = sum(r["freq"] for r in binned["rows"] if r["wc"] >= 5)
    flat_ev = sum(r["ev"] * r["freq"] for r in binned["rows"])
    print(f"  Flat basic strategy: house edge {-flat_ev*100:+.3f}%  "
          f"·  P(buffer count >= +5) = {ge5*100:.2f}%\n")
    print(f"  {'buf count':>9} {'freq%':>7} {'EV%':>8}")
    for r in binned["rows"]:
        if r["n"] < 5000:
            continue
        print(f"  {r['wc']:>+9d} {r['freq']*100:7.2f} {r['ev']*100:+8.3f}")

    print("\n  Window counter (bet up on the buffer count; edge>0 = player):")
    print(f"  {'strategy':>20} {'edge/unit':>10} {'avg bet':>8} {'sd/round':>9}")
    spreads = []
    for name, (ramp, min_bet) in SPREADS.items():
        c = run_counter(rules, ramp, min_bet, a.rounds, cores, a.seed)
        edge = -c["element_of_risk"]            # >0 = player edge per unit wagered
        spreads.append({"name": name, "edge_per_unit": edge, **c})
        print(f"  {name:>20} {edge*100:>+9.3f}% {c['avg_wager_units']:>8.3f} "
              f"{c['std_per_round_units']:>9.2f}")

    print(f"\n  ({time.time() - t0:.0f}s)")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"),
                       "rules_name": rules.name, "buffer": a.buffer,
                       "rounds": a.rounds, "p_ge5": ge5, "flat_house_edge": -flat_ev,
                       "binned": binned["rows"], "spreads": spreads}, f, indent=2)
        print(f"  Wrote {a.out}")


if __name__ == "__main__":
    main()
