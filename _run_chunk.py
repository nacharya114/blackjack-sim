#!/usr/bin/env python3
"""Run a slice [start:end] of the sweep matrix and append results to a JSON list.
Internal helper so a long sweep can be computed in resumable pieces."""
import argparse
import json
import os
import time
from blackjack import run_simulation
from run_sim import build_sweep_matrix

ap = argparse.ArgumentParser()
ap.add_argument("--start", type=int, required=True)
ap.add_argument("--end", type=int, required=True)
ap.add_argument("--rounds", type=int, default=10_000_000)
ap.add_argument("--cores", type=int, default=1)
ap.add_argument("--seed", type=int, default=12345)
ap.add_argument("--dollars-per-unit", type=float, default=10.0)
ap.add_argument("--out", required=True)
a = ap.parse_args()

runs = build_sweep_matrix(100)[a.start:a.end]
acc = []
if os.path.exists(a.out):
    acc = json.load(open(a.out))
for i, (strat, label, r, kw) in enumerate(runs, a.start):
    t = time.time()
    res = run_simulation(r, strat, rounds=a.rounds, strategy_kwargs=kw,
                         cores=max(1, a.cores), dollars_per_unit=a.dollars_per_unit,
                         seed=a.seed)
    res["label"] = label
    res["wall_seconds"] = round(time.time() - t, 1)
    res["cores"] = max(1, a.cores)
    acc.append(res)
    json.dump(acc, open(a.out, "w"))
    print(f"  [{i}] {label:32s} {strat:8s} HE {res['main']['house_edge']*100:+.3f}% "
          f"(+/-{res['hourly']['ci95_round_units']*100:.3f}%)  {res['wall_seconds']:.1f}s", flush=True)
print("CHUNK_DONE", a.start, a.end, flush=True)
