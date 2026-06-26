#!/usr/bin/env python3
"""Generate strategy-panel data: the Hi-Lo deviation table plus count-adjusted
per-action EV curves, and validate that each EV crossover lands on the engine's
published index number.

    python strategy_ev.py --out results/strategy_ev.json

The action whose EV is highest at a given true count is the correct play; where
the basic-action and deviation-action curves cross is the index. If the analytic
crossover and the engine's `ILLUSTRIOUS_18` threshold disagree badly, something
is wrong in one of them -- so this doubles as a cross-check on the strategy.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from blackjack.evcalc import EVModel
from blackjack.strategy import BASIC_HARD, ILLUSTRIOUS_18

ACTION_OF_CODE = {"S": "stand", "H": "hit", "D": "double", "R": "surrender"}
ACTION_LABEL = {"stand": "Stand", "hit": "Hit", "double": "Double",
                "surrender": "Surrender"}


def _col(up: int) -> int:
    return 9 if up == 11 else up - 2


def basic_code(total: int, up: int) -> str:
    return BASIC_HARD[total][_col(up)]


def crossover(grid, basic_ev, dev_ev, direction):
    """True count where deviation EV overtakes basic EV (linear interp)."""
    diff = [d - b for d, b in zip(dev_ev, basic_ev)]
    # Walk the grid; report the first sign change in the favourable direction.
    for i in range(1, len(grid)):
        a, b = diff[i - 1], diff[i]
        if a == 0:
            return grid[i - 1]
        if (a < 0) != (b < 0):                       # sign change
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (-a) / (b - a)
            return round(x, 2)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h17", action="store_true", help="dealer hits soft 17")
    ap.add_argument("--lo", type=float, default=-6.0)
    ap.add_argument("--hi", type=float, default=10.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--out", default="results/strategy_ev.json")
    a = ap.parse_args()

    n = int(round((a.hi - a.lo) / a.step)) + 1
    grid = [round(a.lo + i * a.step, 2) for i in range(n)]
    models = [EVModel(tc, h17=a.h17) for tc in grid]

    hands, table = [], []
    print(f"  Validating EV crossovers vs engine indices (h17={a.h17}):\n")
    print(f"  {'hand':>10} {'basic':>6} {'dev':>6} {'idx':>4} {'crossover':>10} {'ok':>4}")
    for total, soft, up, dev_code, thr, direction in ILLUSTRIOUS_18:
        bcode = basic_code(total, up)
        evs = {k: [] for k in ("stand", "hit", "double", "surrender")}
        for mdl in models:
            ae = mdl.action_evs(total, up, soft=soft)
            for k in evs:
                evs[k].append(round(ae[k], 5))
        basic_act = ACTION_OF_CODE[bcode]
        dev_act = ACTION_OF_CODE[dev_code]
        xo = crossover(grid, evs[basic_act], evs[dev_act], direction)
        ok = xo is not None and abs(xo - thr) <= 1.5
        name = f"{total} v {'A' if up == 11 else up}"
        print(f"  {name:>10} {bcode:>6} {dev_code:>6} {thr:>4} "
              f"{(f'{xo:+.2f}' if xo is not None else 'none'):>10} {'OK' if ok else '!!':>4}")
        rec = {"key": name, "total": total, "up": up, "soft": soft,
               "basic": bcode, "basic_action": basic_act,
               "deviate": dev_code, "deviate_action": dev_act,
               "index": thr, "direction": direction, "crossover": xo, "ev": evs}
        hands.append(rec)
        table.append({"key": name, "total": total, "up": up, "basic": bcode,
                      "deviate": dev_code, "index": thr, "direction": direction})

    # Insurance is a count-only decision (take when EV per unit > 0).
    ins = [round(m.insurance_ev(), 5) for m in models]
    ins_cross = crossover(grid, [0.0] * len(grid), ins, ">=")
    print(f"  {'insurance':>10} {'-':>6} {'Take':>6} {3:>4} "
          f"{(f'{ins_cross:+.2f}' if ins_cross else 'none'):>10} "
          f"{'OK' if ins_cross and abs(ins_cross - 3) <= 1.5 else '!!':>4}")

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "h17": a.h17,
        "tc_grid": grid,
        "hands": hands,
        "insurance": {"ev": ins, "index": 3, "crossover": ins_cross},
        "deviation_table": table,
        "action_label": ACTION_LABEL,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Wrote {a.out}  ({len(hands)} hands + insurance)")


if __name__ == "__main__":
    main()
