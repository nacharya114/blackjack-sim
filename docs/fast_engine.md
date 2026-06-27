# The native (Cython) engine

The simulator ships two interchangeable round engines:

| Engine | Module | Speed (1 core) | When |
|--------|--------|----------------|------|
| **Python** (reference) | `blackjack/game.py` + friends | ~0.09M rounds/s | always available, zero dependencies |
| **Native** (Cython) | `blackjack/_fastsim.pyx` | ~13M rounds/s | optional, built with `make build` |

The native engine is a faithful, branch-for-branch port of the pure-Python
engine that runs the **entire N-round Monte-Carlo loop in C with the GIL
released**. That removes per-round Python overhead and lets multiple cores run as
true threads (no `multiprocessing` spawn/pickle cost). On a 10-core laptop it
turns a 200M-round basic-strategy run from ~40 minutes into a few seconds, which
shrinks the 95% CI on the house edge to **±0.016%** (vs ±0.07% at 10M).

## Building it

```bash
pip install cython setuptools   # or: make dev   (installs both)
make build                  # python setup.py build_ext --inplace
```

You need a C compiler (clang/gcc — Xcode Command Line Tools on macOS). The build
produces `blackjack/_fastsim.<abi>.so`; both that and the generated
`_fastsim.c` are git-ignored. **Nothing else changes** — if you never build it,
the simulator runs exactly as before on pure Python.

## Using it

```bash
# CLI: --engine {auto,python,c}.  Default is "auto" (native if built, else python).
python run_sim.py single --strategy basic --preset vegas_6deck_s17 \
    --rounds 200000000 --cores 0 --engine c

python run_sim.py sweep --rounds 100000000 --cores 0 --engine c
```

```python
from blackjack import run_simulation, Rules
res = run_simulation(Rules(), "basic", rounds=200_000_000, cores=8, engine="c")
print(res["engine"], res["main"]["house_edge"])
```

`run_simulation(..., engine=...)` accepts:

- `"python"` (the library default) — the reference engine.
- `"c"` / `"cython"` / `"fast"` — force the native engine; raises if it isn't built.
- `"auto"` — use the native engine when it's built **and** the config is
  supported, otherwise transparently fall back to Python.

Each result dict now carries an `"engine"` field recording which one actually ran.

## How equivalence is guaranteed

1. **One source of truth for strategy.** At import the extension copies the
   basic-strategy charts, pair tables, and Illustrious-18 indices straight out of
   `blackjack/strategy.py` (`init_from_python`). There is no second hand-edited
   copy: change a chart cell in Python and the C engine picks it up on rebuild.
2. **Statistical cross-check.** `tests/test_fastsim.py` runs both engines on the
   same configs and asserts the native house edge lands within a few standard
   errors of the Python estimate — for the main bet *and* each side bet. It is
   auto-skipped when the extension isn't built, and runs in CI where it is.

## What the native engine does *not* reproduce

The two engines use **different random number generators** — Python's Mersenne
Twister vs. a xoshiro256** seeded by splitmix64 in C. So:

- A given `--seed` reproduces a run **within one engine and a fixed `--cores`**,
  but `--engine c` and `--engine python` will not draw the same cards. Their
  *expected values* match; the individual round outcomes don't.
- This is by design: matching CPython's RNG bit-for-bit in C would forfeit most
  of the speedup for no analytical benefit.

## Supported configs (native path)

The native engine covers every rule the sweep and CLI exercise: 1–8 decks, S17/H17,
peek, DAS/noDAS, double-total restrictions, resplit/hit split aces, late/early
surrender, the full 3:2 / 6:5 payout range, the Hi-Lo counter with an arbitrary
bet ramp + index deviations + insurance, and the Perfect Pairs / 21+3 side bets at
their default paytables. Anything outside that envelope — **custom** side-bet
paytables or >8 decks — makes `engine="auto"` fall back to Python (and
`engine="c"` raise), so results are always correct, just sometimes slower.
