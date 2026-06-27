#!/usr/bin/env python3
"""Cross-check the hand-tuned strategy tables against the analytic EV calculator.

Validator only: the engine keeps using the hardcoded `BASIC_HARD` chart and
`ILLUSTRIOUS_18` indices at runtime -- this proves they are EV-correct (or flags
where they are not, as it did for the 13v2/12v4 no-op bug). It:

  1. checks every hard basic-strategy cell against the EV-optimal action on a
     neutral (true-count 0) deck, and
  2. derives the Hi-Lo index for every borderline hard hand by scanning the true
     count, then diffs the derived set against ILLUSTRIOUS_18.

Scope: hard totals only -- soft hands need a small EV extension and pairs need
split EVs (see docs/strategy_deviations.md). Soft/pair cells are reported as
"not validated" rather than silently skipped.

    python validate_strategy.py            # human-readable report
    python validate_strategy.py --h17      # validate against an H17 game
    python validate_strategy.py --out results/strategy_validation.json
"""
from __future__ import annotations

import argparse
import json
import os

from blackjack.evcalc import EVModel
from blackjack.rules import Rules
from blackjack.strategy import (
    BASIC_HARD, BASIC_SOFT, PAIRS_DAS, ILLUSTRIOUS_18, _basic_hard_action,
)

UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
CODE = {"stand": "S", "hit": "H", "double": "D"}
ACT = {"S": "stand", "H": "hit", "D": "double"}
CANDIDATES = ("stand", "hit", "double")


def upname(up: int) -> str:
    return "A" if up == 11 else str(up)


def best_action(ev: dict) -> str:
    return max(CANDIDATES, key=lambda a: ev[a])


# --- 1. validate the hard basic-strategy chart at true count 0 ----------------
def validate_basic(h17: bool, tol: float):
    model = EVModel(0.0, h17=h17)
    rules = Rules(dealer_hits_soft_17=h17)
    rows, mism, ties = [], [], 0
    for total in range(4, 21):                      # 21 is a trivial stand
        if total not in BASIC_HARD:
            continue
        for up in UPCARDS:
            # the engine's actual basic decision, incl. H17 overrides (11vA -> D)
            code = _basic_hard_action(total, up, rules)
            if code not in ACT:                     # only S/H/D for hard totals
                continue
            ev = model.action_evs(total, up)
            best = best_action(ev)
            gap = ev[best] - ev[ACT[code]]          # EV lost by playing the chart
            if CODE[best] == code:
                status = "ok"
            elif gap < tol:
                status = "tie"
                ties += 1
            else:
                status = "MISMATCH"
            rec = {"total": total, "up": up, "chart": code, "ev_optimal": CODE[best],
                   "ev_gap": round(gap, 5), "status": status}
            rows.append(rec)
            if status == "MISMATCH":
                mism.append(rec)
    return rows, mism, ties


# --- 2. derive Hi-Lo indices for hard hands -----------------------------------
def derive_indices(lo: float, hi: float, step: float):
    n = int(round((hi - lo) / step)) + 1
    grid = [round(lo + step * i, 2) for i in range(n)]
    models = [EVModel(tc) for tc in grid]
    derived = []
    for total in range(4, 21):
        if total not in BASIC_HARD:
            continue
        for up in UPCARDS:
            evs = [m.action_evs(total, up) for m in models]
            seq = [best_action(e) for e in evs]
            for i in range(1, len(grid)):
                if seq[i] == seq[i - 1]:
                    continue
                a0, a1 = seq[i - 1], seq[i]
                d0 = evs[i - 1][a1] - evs[i - 1][a0]
                d1 = evs[i][a1] - evs[i][a0]
                xo = (grid[i - 1] + (grid[i] - grid[i - 1]) * (-d0) / (d1 - d0)
                      if d1 != d0 else grid[i])
                derived.append({"total": total, "up": up, "from": CODE[a0],
                                "to": CODE[a1], "crossover": round(xo, 2)})
    return derived


def match_indices(derived, tol=1.5):
    """Pair each ILLUSTRIOUS_18 entry with the nearest derived crossover for the
    same (total, up); also collect derived deviations not in the table."""
    confirmed, missing = [], []
    used = set()
    for total, _soft, up, dev_code, thr, direction in ILLUSTRIOUS_18:
        cands = [(i, d) for i, d in enumerate(derived)
                 if d["total"] == total and d["up"] == up]
        if not cands:
            missing.append({"hand": f"{total}v{upname(up)}", "index": thr,
                            "derived": None})
            continue
        i, d = min(cands, key=lambda c: abs(c[1]["crossover"] - thr))
        used.add(i)
        confirmed.append({"hand": f"{total}v{upname(up)}", "index": thr,
                          "deviate": dev_code, "derived_cross": d["crossover"],
                          "derived_to": d["to"],
                          "ok": abs(d["crossover"] - thr) <= tol})
    extra = [d for i, d in enumerate(derived)
             if i not in used and -6 <= d["crossover"] <= 8]
    return confirmed, extra


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h17", action="store_true", help="validate against an H17 game")
    ap.add_argument("--tol", type=float, default=0.005,
                    help="EV-unit gap below which a differing cell counts as a tie")
    ap.add_argument("--lo", type=float, default=-7.0)
    ap.add_argument("--hi", type=float, default=10.0)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--out", help="write the full JSON report here")
    a = ap.parse_args()

    rows, mism, ties = validate_basic(a.h17, a.tol)
    checked = len(rows)
    print(f"\n  Basic-strategy hard cells (true count 0, "
          f"{'H17' if a.h17 else 'S17'}):")
    print(f"    {checked} cells checked  ·  {checked - ties - len(mism)} exact  "
          f"·  {ties} ties (<{a.tol} EV)  ·  {len(mism)} mismatches")
    for r in mism:
        print(f"      !! {r['total']:>2} v {upname(r['up']):<2}  chart {r['chart']}  "
              f"EV-optimal {r['ev_optimal']}  (gap {r['ev_gap']:+.4f})")

    derived = derive_indices(a.lo, a.hi, a.step)
    confirmed, extra = match_indices(derived)
    bad = [c for c in confirmed if not c["ok"]]
    print(f"\n  Hi-Lo indices: {len(confirmed)} Illustrious-18 entries, "
          f"{len(confirmed) - len(bad)} confirmed within 1.5 of derived crossover.")
    for c in confirmed:
        flag = "  " if c["ok"] else "!!"
        print(f"    {flag} {c['hand']:>7}  index {c['index']:>+2}  "
              f"derived {c['derived_cross']:>+5.2f} -> {c['derived_to']}")
    if extra:
        print("\n  Derived hard deviations NOT in the Illustrious 18 "
              "(informational, |TC|<=8):")
        for d in sorted(extra, key=lambda d: (d["total"], d["up"])):
            print(f"      {d['total']:>2} v {upname(d['up']):<2}  "
                  f"{d['from']}->{d['to']} at TC {d['crossover']:+.2f}")

    print(f"\n  Not validated (need soft / split EVs): "
          f"{len(BASIC_SOFT)} soft totals + {len(PAIRS_DAS)} pair rows.")

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"h17": a.h17, "tol": a.tol, "basic_cells": rows,
                       "basic_mismatches": mism, "indices_confirmed": confirmed,
                       "indices_extra": extra}, f, indent=2)
        print(f"\n  Wrote {a.out}")

    # Non-zero exit if the chart disagrees with EV beyond tie tolerance.
    raise SystemExit(1 if (mism or bad) else 0)


if __name__ == "__main__":
    main()
