#!/usr/bin/env python3
"""Generate the full basic-strategy *chart* with count deviations and per-action
EVs, for the dashboard's strategy-chart panel.

    python strategy_chart.py --out results/strategy_chart.json   # add --h17 for H17

Unlike strategy_ev.py (which covers the ~15 Illustrious-18 hands as EV curves),
this walks **every** chart cell -- pairs, soft totals, hard totals -- straight
out of the engine's own decision functions, so the rendered chart is always in
sync with `blackjack/strategy.py`. For each (hand, dealer-upcard) cell it records:

  * the basic-strategy action (count-independent), and
  * the action the Hi-Lo counter actually plays at each true count on a grid,
    so the dashboard can highlight the cells that deviate at a chosen count;

plus per-action EV curves (stand / hit / double) from `blackjack/evcalc.py`, used
for the "Show action EVs" detail + decision-margin heat tint. EVs are exact for
hard and soft totals; pair rows additionally carry a **split** EV curve
(`evcalc.split_ev`, ruleset-aware: DAS, resplits, split-aces), so the dashboard
can show why splitting wins (or loses) versus playing the hand as its total.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from blackjack.cards import hand_total
from blackjack.evcalc import EVModel
from blackjack.rules import Rules
from blackjack.strategy import basic_action, counter_action, take_insurance

UPCARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
PAIR_VALUES = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]   # A,A  T,T  9,9 ... 2,2
SOFT_KICKERS = [9, 8, 7, 6, 5, 4, 3, 2]          # A,9 (soft 20) ... A,2 (soft 13)
HARD_TOTALS = list(range(17, 7, -1))             # 17 down to 8


def card_for_value(v: int, suit: int = 0) -> int:
    """A representative card int for a blackjack value (11=ace, 10=any ten)."""
    if v == 11:
        rank = 12
    elif v == 10:
        rank = 8
    else:
        rank = v - 2
    return rank + 13 * suit


def hard_cards(total: int) -> list[int]:
    """Two distinct non-ace, non-pair cards summing to `total` (a clean hard hand)."""
    for a in range(2, 11):
        b = total - a
        if 2 <= b <= 10 and a != b:
            return [card_for_value(a, 0), card_for_value(b, 1)]
    raise ValueError(f"no clean hard two-card hand for total {total}")


def pair_label(v: int) -> str:
    return "A,A" if v == 11 else ("T,T" if v == 10 else f"{v},{v}")


def cell_for(cards, up, is_soft, rules, tc_grid, models, pair_val=None):
    """Build one chart cell: basic action, per-TC action, and EV curves.

    `pair_val` (the pair's card value, 11 = aces) enables the split-EV curve."""
    total = hand_total(cards)[0]
    can_double = rules.can_double_total(min(total, 21))
    can_split = True   # the engine only splits when the hand is actually a pair
    can_surrender = rules.late_surrender or rules.early_surrender

    basic = basic_action(cards, up, can_double=can_double, can_split=can_split,
                         can_surrender=can_surrender, rules=rules)
    acts = [counter_action(cards, up, float(tc), can_double=can_double,
                           can_split=can_split, can_surrender=can_surrender,
                           rules=rules) for tc in tc_grid]

    ev = {"stand": [], "hit": [], "double": []}
    if pair_val is not None:
        ev["split"] = []
    for mdl in models:
        ae = mdl.action_evs(min(total, 21), up, soft=is_soft, pair=pair_val,
                            das=rules.double_after_split,
                            max_hands=rules.max_split_hands,
                            resplit_aces=rules.resplit_aces,
                            hit_split_aces=rules.hit_split_aces)
        ev["stand"].append(round(ae["stand"], 4))
        ev["hit"].append(round(ae["hit"], 4))
        ev["double"].append(round(ae["double"], 4))
        if pair_val is not None:
            ev["split"].append(round(ae["split"], 4))
    return {"basic": basic, "acts": acts, "ev": ev}


def build_section(name, kind, rows_spec, rules, tc_grid, models):
    rows = []
    for label, cards, is_soft, pair_val in rows_spec:
        cells = [cell_for(cards, up, is_soft, rules, tc_grid, models, pair_val)
                 for up in UPCARDS]
        rows.append({"label": label, "ev_kind": "soft" if is_soft else "hard",
                     "is_pair": pair_val is not None, "cells": cells})
    return {"name": name, "kind": kind, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h17", action="store_true", help="dealer hits soft 17")
    ap.add_argument("--lo", type=int, default=-6)
    ap.add_argument("--hi", type=int, default=10)
    ap.add_argument("--out", default="results/strategy_chart.json")
    a = ap.parse_args()

    rules = Rules(dealer_hits_soft_17=a.h17)
    tc_grid = list(range(a.lo, a.hi + 1))
    models = [EVModel(float(tc), h17=a.h17) for tc in tc_grid]

    pairs_spec = [(pair_label(v),
                   [card_for_value(v, 0), card_for_value(v, 1)],
                   v == 11, v) for v in PAIR_VALUES]
    soft_spec = [(f"A,{k}", [card_for_value(11, 0), card_for_value(k, 1)], True, None)
                 for k in SOFT_KICKERS]
    hard_spec = [(str(t), hard_cards(t), False, None) for t in HARD_TOTALS]

    sections = [
        build_section("Pairs", "pairs", pairs_spec, rules, tc_grid, models),
        build_section("Soft totals", "soft", soft_spec, rules, tc_grid, models),
        build_section("Hard totals", "hard", hard_spec, rules, tc_grid, models),
    ]

    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "h17": a.h17,
        "rules_name": rules.name,
        "tc_grid": tc_grid,
        "upcards": UPCARDS,
        "sections": sections,
        "insurance_index": 3,
        "insurance_take_at": [1 if take_insurance(float(tc)) else 0 for tc in tc_grid],
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    base_idx = tc_grid.index(0)
    n_cells = sum(len(r["cells"]) for s in sections for r in s["rows"])
    devs0 = sum(1 for s in sections for r in s["rows"] for c in r["cells"]
                if c["acts"][base_idx] != c["basic"])
    print(f"  Wrote {a.out}")
    print(f"  {len(sections)} sections, {n_cells} cells, "
          f"TC grid {tc_grid[0]}..{tc_grid[-1]} ({len(tc_grid)} points), "
          f"{'H17' if a.h17 else 'S17'}")
    print(f"  deviations vs basic at TC 0: {devs0}")


if __name__ == "__main__":
    main()
