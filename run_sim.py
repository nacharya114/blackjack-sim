#!/usr/bin/env python3
"""Command-line driver for the blackjack EV / house-edge simulator.

Examples
--------
Single run, default Vegas 6-deck game, 5M rounds, basic strategy:
    python run_sim.py single --rounds 5000000 --cores 8

Counter on a deeply-dealt good game with a 1-12 spread:
    python run_sim.py single --strategy counter --penetration 0.85 --rounds 10000000 --cores 8

Add a side bet:
    python run_sim.py single --sidebets perfect_pairs 21+3 --rounds 3000000

Full comparison matrix for the dashboard:
    python run_sim.py sweep --rounds 2000000 --cores 8 --out results/sweep.json
"""

from __future__ import annotations

import argparse
import json
import os
import time

from blackjack import Rules, PRESETS, run_simulation
from blackjack.sidebets import SIDE_BETS


def resolve_cores(cores) -> int:
    """`--cores 0` (or negative) means: use every logical core on this machine."""
    if cores is None or cores <= 0:
        return os.cpu_count() or 1
    return max(1, int(cores))


def rules_from_args(args) -> Rules:
    if args.preset:
        base = PRESETS[args.preset]
        r = Rules.from_dict(base.to_dict())
    elif args.config:
        with open(args.config) as f:
            r = Rules.from_dict(json.load(f))
    else:
        r = Rules()
    # explicit flags override
    if args.decks is not None:
        r.decks = args.decks
    if args.penetration is not None:
        r.penetration = args.penetration
    if args.h17:
        r.dealer_hits_soft_17 = True
    if args.s17:
        r.dealer_hits_soft_17 = False
    if getattr(args, "csm", False):
        r.csm = True
    if getattr(args, "csm_buffer", None):
        r.csm_buffer = args.csm_buffer
    if args.das is not None:
        r.double_after_split = args.das
    if args.ls is not None:
        r.late_surrender = args.ls
    if args.payout is not None:
        r.blackjack_payout = args.payout
    if args.hands_per_hour is not None:
        r.hands_per_hour = args.hands_per_hour
    return r


def parse_bet_ramp(spec: str) -> dict:
    """Parse a "tc:bet,tc:bet,..." spread spec, e.g. "1:1,2:2,3:4,4:6,5:8"."""
    ramp = {}
    for pair in spec.split(","):
        tc, bet = pair.split(":")
        ramp[int(tc)] = float(bet)
    if not ramp:
        raise ValueError("empty bet ramp")
    return ramp


def strategy_kwargs_from_args(args) -> dict:
    kw = {}
    if args.sidebets:
        kw["side_bets"] = tuple(args.sidebets)
        kw["side_bet_unit"] = args.side_bet_unit
    # Custom counter bet spread (only meaningful for --strategy counter).
    if getattr(args, "bet_ramp", None):
        kw["bet_ramp"] = parse_bet_ramp(args.bet_ramp)
    if getattr(args, "min_bet", None) is not None:
        kw["min_bet"] = args.min_bet
    return kw


def print_summary(res: dict) -> None:
    m = res["main"]
    h = res["hourly"]
    print(f"\n  Ruleset      : {res['rules_name']}")
    print(f"  Strategy     : {res['strategy']}   rounds: {res['rounds']:,}"
          f"   engine: {res.get('engine', 'python')}")
    print(f"  House edge   : {m['house_edge']*100:+.3f}%  (per original wager)")
    print(f"  Elem of risk : {m['element_of_risk']*100:+.3f}%  (per total risked)")
    print(f"  EV / round   : {res['total']['ev_per_round_units']:+.5f} units"
          f"   (main+side, total HE {res['total']['house_edge']*100:+.3f}%)")
    print(f"  Std / round  : {res['total']['std_per_round_units']:.3f} units")
    print(f"  Hourly EV    : {h['ev_units']:+.4f} units "
          f"= ${h['ev_dollars']:+.2f}  (+/- ${h['ci95_hour_dollars']:.2f} 95% CI)"
          f"  @ {res['hands_per_hour']} hands/hr, ${res['dollars_per_unit']}/unit")
    print(f"  Hourly sigma : ${h['std_dollars']:.2f}")
    if res["side_bets"]:
        print("  Side bets:")
        for name, s in res["side_bets"].items():
            print(f"    {name:16s} HE {s['house_edge']*100:+.3f}%   "
                  f"EV/round {s['ev_per_round_units']:+.5f} units")


def cmd_single(args):
    rules = rules_from_args(args)
    kw = strategy_kwargs_from_args(args)
    cores = resolve_cores(args.cores)
    print(f"  Running {args.rounds:,} rounds on {cores} core(s) [{args.engine} engine]...")
    t = time.time()
    res = run_simulation(rules, args.strategy, rounds=args.rounds,
                         strategy_kwargs=kw, cores=cores,
                         dollars_per_unit=args.dollars_per_unit, seed=args.seed,
                         engine=args.engine)
    res["wall_seconds"] = round(time.time() - t, 1)
    res["cores"] = cores
    print_summary(res)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(res, f, indent=2)
        print(f"\n  Wrote {args.out}")


def build_sweep_matrix(hands_per_hour=100):
    """Return the list of (strategy, label, Rules, strategy_kwargs) for the sweep.

    Shared by `cmd_sweep` and any external chunked runner so the matrix is
    defined in exactly one place.
    """
    hph = hands_per_hour or 100
    rulesets = {
        "6D S17 DAS LS 3:2": Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                                   late_surrender=True, blackjack_payout=1.5, penetration=0.75,
                                   hands_per_hour=hph),
        "6D H17 DAS noLS 3:2": Rules(decks=6, dealer_hits_soft_17=True, double_after_split=True,
                                     late_surrender=False, blackjack_payout=1.5, penetration=0.75,
                                     hands_per_hour=hph),
        "2D H17 DAS 3:2": Rules(decks=2, dealer_hits_soft_17=True, double_after_split=True,
                                late_surrender=False, blackjack_payout=1.5, penetration=0.75,
                                hands_per_hour=hph),
        "1D S17 noDAS 3:2": Rules(decks=1, dealer_hits_soft_17=False, double_after_split=False,
                                  late_surrender=False, blackjack_payout=1.5, penetration=0.65,
                                  hands_per_hour=hph),
        "6D H17 noDAS 6:5": Rules(decks=6, dealer_hits_soft_17=True, double_after_split=False,
                                  late_surrender=False, blackjack_payout=1.2, penetration=0.75,
                                  hands_per_hour=hph),
    }
    counter_rules = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                          late_surrender=True, blackjack_payout=1.5, penetration=0.85,
                          hands_per_hour=hph)
    # Continuous shuffle machine, matched to the 6D S17 shoe game above. A CSM
    # reshuffles every round, so counting is dead -- but it deals faster, so we
    # model ~30% more hands/hour (the casino's real edge).
    csm_rules = Rules(decks=6, csm=True, dealer_hits_soft_17=False, double_after_split=True,
                      late_surrender=True, blackjack_payout=1.5, hands_per_hour=int(hph * 1.3))

    runs = []
    for name, r in rulesets.items():
        runs.append(("basic", name, r, {}))
    runs.append(("counter", "6D S17 DAS LS 3:2 (deep)", counter_rules, {}))
    runs.append(("basic", "6D CSM S17 DAS LS 3:2", csm_rules, {}))
    runs.append(("counter", "6D CSM S17 DAS LS 3:2 (counter)", csm_rules, {}))
    main = rulesets["6D S17 DAS LS 3:2"]
    runs.append(("basic", "6D S17 + Perfect Pairs", main,
                 {"side_bets": ("perfect_pairs",), "side_bet_unit": 1.0}))
    runs.append(("basic", "6D S17 + 21+3", main,
                 {"side_bets": ("21+3",), "side_bet_unit": 1.0}))
    runs.append(("basic", "6D S17 + both sidebets", main,
                 {"side_bets": ("perfect_pairs", "21+3"), "side_bet_unit": 1.0}))
    return runs


def cmd_sweep(args):
    """Run a matrix of rulesets x strategies x side-bet configs for the dashboard."""
    runs = build_sweep_matrix(args.hands_per_hour)

    results = []
    cores = resolve_cores(args.cores)
    total = len(runs)
    print(f"  Sweep: {total} configs x {args.rounds:,} rounds on {cores} core(s) "
          f"[{args.engine} engine]\n")
    sweep_start = time.time()
    for idx, (strat, label, r, kw) in enumerate(runs, 1):
        t = time.time()
        res = run_simulation(r, strat, rounds=args.rounds, strategy_kwargs=kw,
                             cores=cores, dollars_per_unit=args.dollars_per_unit,
                             seed=args.seed, engine=args.engine)
        res["label"] = label
        res["wall_seconds"] = round(time.time() - t, 1)
        res["cores"] = cores
        results.append(res)
        done = time.time() - sweep_start
        eta = done / idx * (total - idx)
        print(f"  [{idx}/{total}] [{res['wall_seconds']:6.1f}s] {label:32s} {strat:8s} "
              f"HE {res['main']['house_edge']*100:+.3f}% "
              f"(+/-{res['hourly']['ci95_round_units']*100:.3f}%)  "
              f"hourly ${res['hourly']['ev_dollars']:+.2f}   eta {eta/60:4.1f}m")

    payload = {"generated": time.strftime("%Y-%m-%d %H:%M"),
               "rounds_each": args.rounds,
               "cores": cores,
               "dollars_per_unit": args.dollars_per_unit,
               "results": results}
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Wrote {args.out}  ({len(results)} runs)")
    print("  Build the dashboard with:  python visualize.py --data", args.out)


def build_parser():
    p = argparse.ArgumentParser(description="Blackjack EV / house-edge simulator")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--rounds", type=int, default=2_000_000)
        sp.add_argument("--cores", type=int, default=1,
                        help="worker processes; 0 = use all logical cores on this machine")
        sp.add_argument("--seed", type=int, default=None)
        sp.add_argument("--dollars-per-unit", type=float, default=10.0)
        sp.add_argument("--hands-per-hour", type=int, default=None)
        sp.add_argument("--engine", choices=["auto", "python", "c"], default="auto",
                        help="round engine: 'c' = native Cython (fast), 'python' = "
                             "pure-stdlib reference, 'auto' = native if built (default)")

    sp = sub.add_parser("single", help="run one configuration")
    add_common(sp)
    sp.add_argument("--strategy", default="basic",
                    choices=["basic", "counter", "window_counter"])
    sp.add_argument("--preset", choices=list(PRESETS))
    sp.add_argument("--config", help="path to a rules JSON file")
    sp.add_argument("--decks", type=int)
    sp.add_argument("--penetration", type=float)
    sp.add_argument("--s17", action="store_true", help="dealer stands soft 17")
    sp.add_argument("--h17", action="store_true", help="dealer hits soft 17")
    sp.add_argument("--csm", action="store_true",
                    help="continuous shuffle machine: reshuffle every round "
                         "(kills card counting; penetration is ignored)")
    sp.add_argument("--csm-buffer", type=int, default=None,
                    help="windowed CSM: hold the last N dealt cards out of the pool "
                         "(a partial reservoir, e.g. 16). Use with --strategy window_counter")
    sp.add_argument("--das", dest="das", action="store_true", default=None)
    sp.add_argument("--no-das", dest="das", action="store_false")
    sp.add_argument("--ls", dest="ls", action="store_true", default=None,
                    help="late surrender on")
    sp.add_argument("--no-ls", dest="ls", action="store_false")
    sp.add_argument("--payout", type=float, help="blackjack payout multiple (1.5=3:2, 1.2=6:5)")
    sp.add_argument("--sidebets", nargs="*", choices=list(SIDE_BETS))
    sp.add_argument("--side-bet-unit", type=float, default=1.0)
    sp.add_argument("--bet-ramp", help="counter bet spread as 'tc:bet,...' "
                    "e.g. '1:1,2:2,3:3,4:4,5:5' for a 1-5 spread maxing at TC+5")
    sp.add_argument("--min-bet", type=float, help="counter bet at true counts "
                    "below the ramp (default 1.0)")
    sp.add_argument("--out", help="write result JSON here")
    sp.set_defaults(func=cmd_single)

    sw = sub.add_parser("sweep", help="run the full comparison matrix for the dashboard")
    add_common(sw)
    sw.add_argument("--out", default="results/sweep.json")
    sw.set_defaults(func=cmd_sweep)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
