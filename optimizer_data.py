#!/usr/bin/env python3
"""Generate the data for the dashboard's **bet-spread optimizer** tab.

The optimizer tab is a live, closed-form model: drag rules / penetration / table
cap / per-count bet sizes and read EV, sigma, SCORE instantly, with no
simulation in the browser. The trick is that *everything* a bet ramp earns
reduces to a weighted sum over the **true-count buckets** of a game:

    for each integer true count TC (at bet time), measure once from the engine
        p(TC) = how often you are at that count          (decks + penetration)
        m(TC) = EV per initial unit wagered at that count (rules + index plays)
        v(TC) = variance per initial unit at that count   (~1.30, very flat)

Then for ANY bet ramp b(TC):

    EV/round  = sum_TC  p*b*m
    Var/round = sum_TC  p*b^2*(v + m^2)  -  EV^2
    SCORE     = (EV/sigma)^2 * 1e6        (scale-free risk efficiency = 1e6 / N0)

This script measures {p, m, v} from the (pure-Python) engine for a grid of rule
sets and penetrations -- using a flat-betting Hi-Lo counter that plays the
Illustrious-18 index deviations, so m(TC) already includes count-dependent play
and insurance -- and writes results/optimizer.json. For each config it also
simulates one reference ramp end-to-end (`run_simulation`) and stores both the
Monte-Carlo and the closed-form numbers, so the dashboard can show the
closed-form model agreeing with the engine (honest validation, not a black box).

Usage:
    python optimizer_data.py --rounds 12000000 --cores 0 --out results/optimizer.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from collections import defaultdict

from blackjack import Rules, run_simulation
from blackjack.game import play_round
from blackjack.players import CardCounter
from blackjack.shoe import Shoe

# Buckets outside this true-count range are lumped into the end bins (rare, and
# we sit out the deep-negative end anyway). Covers the meaningful betting range.
TC_MIN, TC_MAX = -5, 8


def _bucket_chunk(args):
    """Worker: play `n_rounds` flat-bet counter rounds, tally net by floor(TC)."""
    rules_dict, n_rounds, seed = args
    rules = Rules.from_dict(rules_dict)
    strat = CardCounter(bet_ramp={0: 1}, min_bet=1.0)   # flat 1u, plays I18 + insurance
    rng = random.Random(seed)
    shoe = Shoe(rules.decks, rules.penetration, rng=rng, csm_buffer=rules.csm_buffer)
    n = defaultdict(int)
    s = defaultdict(float)
    s2 = defaultdict(float)
    for _ in range(n_rounds):
        if rules.csm or shoe.needs_shuffle() or shoe.cards_remaining < 20:
            shoe.shuffle()
        tc = int(shoe.true_count() // 1)                # floor, the betting count
        b = TC_MIN if tc < TC_MIN else (TC_MAX if tc > TC_MAX else tc)
        r = play_round(shoe, rules, strat)
        n[b] += 1
        s[b] += r.main_net
        s2[b] += r.main_net * r.main_net
    return n, s, s2


def measure_buckets(rules: Rules, rounds: int, cores: int, seed: int) -> list[dict]:
    """Return [{tc, p, m, v}] -- frequency, EV/unit and variance/unit by true count."""
    cores = max(1, cores)
    if cores == 1:
        parts = [_bucket_chunk((rules.to_dict(), rounds, seed))]
    else:
        import multiprocessing as mp
        per = rounds // cores
        chunks = [per] * cores
        chunks[-1] += rounds - per * cores
        jobs = [(rules.to_dict(), c, seed + i) for i, c in enumerate(chunks)]
        with mp.Pool(cores) as pool:
            parts = pool.map(_bucket_chunk, jobs)

    n = defaultdict(int)
    s = defaultdict(float)
    s2 = defaultdict(float)
    for pn, ps, ps2 in parts:
        for k, v in pn.items():
            n[k] += v
        for k, v in ps.items():
            s[k] += v
        for k, v in ps2.items():
            s2[k] += v

    total = sum(n.values())
    out = []
    for tc in range(TC_MIN, TC_MAX + 1):
        if not n[tc]:
            continue
        m = s[tc] / n[tc]
        var = max(0.0, s2[tc] / n[tc] - m * m)
        out.append({"tc": tc, "p": n[tc] / total, "m": m, "v": var,
                    "n": n[tc]})
    return out


def closed_form_metrics(buckets: list[dict], bet_of) -> dict:
    """EV/round, sigma/round, SCORE, N0 and house edge for a ramp, from buckets.

    `bet_of(tc) -> units` is the bet ramp (0 = sit out). This is the exact same
    weighted sum the dashboard JS computes; it is the source of truth for both.
    """
    ev = 0.0
    e2 = 0.0
    avg_bet = 0.0
    for bk in buckets:
        b = bet_of(bk["tc"])
        ev += bk["p"] * b * bk["m"]
        e2 += bk["p"] * b * b * (bk["v"] + bk["m"] * bk["m"])
        avg_bet += bk["p"] * b
    var = max(0.0, e2 - ev * ev)
    sd = math.sqrt(var)
    return {
        "ev_per_round_units": ev,
        "std_per_round_units": sd,
        "score": (ev / sd) ** 2 * 1e6 if sd > 0 and ev > 0 else 0.0,
        "n0_rounds": (sd / ev) ** 2 if ev > 0 else None,
        "house_edge": (-ev / avg_bet) if avg_bet > 0 else 0.0,
        "avg_initial_units": avg_bet,
    }


def ramp_bet_of(ramp: dict[int, float], min_bet: float):
    """A bet function from an explicit {tc: bet} map: min_bet below the lowest key,
    the top value at/above the highest key."""
    lo, hi = min(ramp), max(ramp)
    return lambda tc: (min_bet if tc < lo else
                       float(ramp[hi]) if tc >= hi else float(ramp.get(tc, min_bet)))


# The grid of rule sets x penetrations the optimizer tab can choose between.
def build_config_grid():
    def R(**kw):
        return Rules(blackjack_payout=kw.pop("payout", 1.5), hands_per_hour=100, **kw)
    return [
        ("6D S17 DAS LS 3:2", 0.75,
         R(decks=6, dealer_hits_soft_17=False, double_after_split=True,
           late_surrender=True, penetration=0.75)),
        ("6D S17 DAS LS 3:2 (deep)", 0.85,
         R(decks=6, dealer_hits_soft_17=False, double_after_split=True,
           late_surrender=True, penetration=0.85)),
        ("6D H17 DAS noLS 3:2", 0.75,
         R(decks=6, dealer_hits_soft_17=True, double_after_split=True,
           late_surrender=False, penetration=0.75)),
        ("2D H17 DAS 3:2", 0.75,
         R(decks=2, dealer_hits_soft_17=True, double_after_split=True,
           late_surrender=False, penetration=0.75)),
        ("6D H17 noDAS 6:5", 0.75,
         R(decks=6, dealer_hits_soft_17=True, double_after_split=False,
           late_surrender=False, payout=1.2, penetration=0.75)),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=12_000_000,
                    help="rounds per config for the bucket measurement")
    ap.add_argument("--rounds-validate", type=int, default=40_000_000,
                    help="rounds for the end-to-end Monte-Carlo validation ramp")
    ap.add_argument("--cores", type=int, default=0, help="0 = all logical cores")
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--out", default="results/optimizer.json")
    a = ap.parse_args()

    cores = (os.cpu_count() or 1) if a.cores <= 0 else a.cores
    # Reference ramp used to validate closed-form vs Monte-Carlo (Wong out < -1).
    ref_ramp = {-1: 1, 0: 1, 1: 1, 2: 4, 3: 8, 4: 12}
    ref_min_bet = 0.0

    grid = build_config_grid()
    print(f"  optimizer data: {len(grid)} configs x {a.rounds:,} bucket rounds "
          f"on {cores} cores\n")
    configs = []
    t0 = time.time()
    for i, (label, pen, rules) in enumerate(grid, 1):
        t = time.time()
        buckets = measure_buckets(rules, a.rounds, cores, a.seed + i)

        # Closed-form vs Monte-Carlo on the reference ramp (honest validation).
        cf = closed_form_metrics(buckets, ramp_bet_of(ref_ramp, ref_min_bet))
        mc = run_simulation(rules, "counter", rounds=a.rounds_validate,
                            strategy_kwargs={"bet_ramp": dict(ref_ramp),
                                             "min_bet": ref_min_bet},
                            cores=cores, seed=a.seed, engine="auto")
        validation = {
            "ramp": dict(sorted(ref_ramp.items())), "min_bet": ref_min_bet,
            "closed_form": {k: cf[k] for k in ("ev_per_round_units",
                                               "std_per_round_units", "score")},
            "monte_carlo": {
                "ev_per_round_units": mc["total"]["ev_per_round_units"],
                "std_per_round_units": mc["total"]["std_per_round_units"],
                "score": ((mc["total"]["ev_per_round_units"]
                           / mc["total"]["std_per_round_units"]) ** 2 * 1e6
                          if mc["total"]["ev_per_round_units"] > 0 else 0.0),
                "rounds": mc["rounds"], "engine": mc["engine"],
            },
        }

        configs.append({
            "id": label.replace(" ", "_").replace(":", "").replace("(", "").replace(")", ""),
            "label": label,
            "rules_name": rules.name,
            "decks": rules.decks,
            "penetration": pen,
            "h17": rules.dealer_hits_soft_17,
            "das": rules.double_after_split,
            "ls": rules.late_surrender,
            "payout": rules.blackjack_payout,
            "rounds": a.rounds,
            "tc_min": TC_MIN, "tc_max": TC_MAX,
            "buckets": [{"tc": b["tc"], "p": b["p"], "m": b["m"], "v": b["v"]}
                        for b in buckets],
            "validation": validation,
        })
        cfd = validation["closed_form"]
        mcd = validation["monte_carlo"]
        print(f"  [{i}/{len(grid)}] {label:26s} pen {pen:.2f}  "
              f"[{time.time()-t:5.0f}s]  validate ref-ramp: "
              f"CF EV {cfd['ev_per_round_units']:+.5f} / MC EV {mcd['ev_per_round_units']:+.5f}  "
              f"CF SCORE {cfd['score']:5.1f} / MC {mcd['score']:5.1f}")

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "rounds_each": a.rounds,
        "tc_min": TC_MIN, "tc_max": TC_MAX,
        "configs": configs,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Wrote {a.out}  ({len(configs)} configs, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
