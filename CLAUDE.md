# CLAUDE.md — project guide for Claude Code

Monte-Carlo blackjack simulator that measures **house edge / EV** across
strategies, rule sets, side bets, and card-counter **bet spreads**, plus an
offline HTML dashboard. The engine is **pure Python standard library** (no
runtime dependencies); `matplotlib` is used solely for optional PNG charts. An
**optional Cython "native" engine** (`blackjack/_fastsim.pyx`) ports the round
loop to `nogil` C for a ~100–150x speedup — build it with `make build`; without
it, everything falls back to pure Python (see `docs/fast_engine.md`).

## Run it

```bash
# Full comparison matrix on all cores, then build the dashboard
python run_sim.py sweep --rounds 10000000 --cores 0 --seed 12345
python visualize.py --data results/sweep.json
# Open results/dashboard.html (offline, just double-click)

# Build the optional native engine once, then run with it (auto-selected by default)
make build      # python setup.py build_ext --inplace  (needs cython + a C compiler)
python run_sim.py single --strategy basic --preset vegas_6deck_s17 \
    --rounds 200000000 --cores 0 --engine c    # 200M rounds in seconds

# One configuration
python run_sim.py single --strategy basic --preset vegas_6deck_s17 --rounds 10000000 --cores 0

# Card counter with a custom bet spread (1-8 ramp maxing at TC+5)
python run_sim.py single --strategy counter --decks 6 --s17 --das --no-ls \
    --penetration 0.75 --bet-ramp "1:1,2:3,3:4,4:6,5:8" --rounds 10000000 --cores 0

# Bet-spread breakeven search -> results/betspread_*.json (feeds a dashboard panel)
python betspread.py --pen 0.75 --rounds 12000000 --cores 0 --out results/betspread_pen75.json
```

`--cores 0` = every logical core. More `--rounds` = tighter CI (±0.07% at ~12M).
`make help` lists convenience targets (`make test`, `make sweep`, `make dashboard`, …).

## Architecture

Data flow: **`Rules` + a strategy → `simulator.run_simulation` loops
`game.play_round` over a `Shoe` → a JSON results dict → `visualize` inlines it
into a self-contained `dashboard.html`.**

- `blackjack/` — the engine (no third-party imports):
  - `cards.py` — card = int 0..51; precomputed lookup tuples (`BJ_VALUE`,
    `HILO`, `SUIT`, …); `hand_total`, `is_blackjack`.
  - `rules.py` — `Rules` dataclass (every edge-affecting table rule) + `PRESETS`.
    Includes `csm` (continuous shuffle machine): when set, `game.play_round` and
    the native engine reshuffle a full N-deck stack every round, so the count
    never builds (see `docs/csm.md`).
  - `shoe.py` — `Shoe`: shuffled multi-deck stack, cut card, Hi-Lo running/true count.
  - `strategy.py` — basic-strategy tables + Hi-Lo index deviations (Illustrious 18).
  - `players.py` — `BasicStrategy` (flat), `CardCounter` (`bet_ramp` → bet by
    floor(true count); plays index deviations + insurance), and `WindowCounter`
    (bets on a windowed CSM's buffer count; plays basic strategy). `build_strategy()`.
  - `sidebets.py` — Perfect Pairs & 21+3 paytables and resolution.
  - `game.py` — one full round: splits, doubles, surrender, dealer play, payout.
  - `simulator.py` — Monte-Carlo driver + EV/variance statistics. `run_simulation`
    takes `engine=` (`"python"` | `"c"`/`"cython"` | `"auto"`); the Python path
    uses multiprocessing, the native path uses GIL-releasing threads. `_run_c`
    builds the C config tuples; `fast_available()` reports whether the extension
    is built. Custom paytables / >8 decks fall back to Python under `"auto"`.
  - `_fastsim.pyx` — **optional** Cython port of the whole round loop (shoe,
    strategy decisions, splits, side bets) running `nogil`. Strategy tables are
    copied from `strategy.py` at import (`init_from_python`), so there is **one
    source of truth** — editing the Python charts re-tunes the C engine. Uses a
    xoshiro256** RNG (different stream from CPython, so not bit-identical to the
    Python engine for a given seed, but the EV is identical — proven by tests).
- `run_sim.py` — CLI (`single`, `sweep`). `build_sweep_matrix()` defines the
  dashboard matrix in one place. `single` accepts `--bet-ramp` / `--min-bet`.
- `betspread.py` — sweeps a counter's bet spread to find the lowest-risk
  breakeven spread (see `docs/bet_spreads.md`).
- `blackjack/evcalc.py` + `strategy_ev.py` — analytic count-adjusted per-action
  EV calculator and the generator for the dashboard's strategy-deviation panel;
  self-validates EV crossovers against the engine indices (see
  `docs/strategy_deviations.md`). `evcalc.action_evs` handles **hard and soft**
  totals (pass `soft=`); only split EVs are still missing (roadmap #2).
- `strategy_chart.py` — generates `results/strategy_chart.json` for the dashboard's
  **basic-strategy-chart panel**: walks every pairs/soft/hard cell straight out of
  the engine's `basic_action`/`counter_action`, recording the basic action, the
  count action at each true count on a grid (for the deviation slider), and
  per-action EV curves from `evcalc`. Always in sync with `strategy.py`.
- `validate_strategy.py` — offline cross-check (`make validate`) that proves the
  hardcoded `BASIC_HARD` chart and `ILLUSTRIOUS_18` indices are EV-optimal
  (hard totals); guarded in CI by `tests/test_strategy_validation.py`.
- `visualize.py` — builds `dashboard.html` (sweep data + any `betspread_*.json`
  + `strategy_ev.json` + `strategy_chart.json` inlined at `/*__…__*/` markers)
  and optional PNGs. `make strategy` regenerates both strategy JSONs.
- `_run_chunk.py` — internal helper to compute a slice of the sweep resumably.
- `setup.py` — builds `blackjack._fastsim` via `cythonize` (`make build`); the
  generated `.c`/`.so` are git-ignored, only `_fastsim.pyx` is tracked.
- `configs/` example rules JSON · `docs/` analysis writeups · `results/`
  generated data + dashboard.

## Development

```bash
make dev       # pip install -r requirements-dev.txt  (pytest, ruff, cython, matplotlib)
make build     # compile the native engine in place (python setup.py build_ext --inplace)
make test      # pytest  (fast: small fixed-seed sims + unit tests, ~15s)
make lint      # ruff check .
make validate  # cross-check the strategy tables against the EV calculator
```

Tests live in `tests/`: `test_engine_units.py` (cards/rules/shoe/sidebets,
deterministic), `test_simulation.py` (small fixed-seed integration runs asserting
magnitude bands + directional invariants — robust to Monte-Carlo noise),
`test_tools.py` (betspread math + dashboard inlining; no matplotlib needed),
`test_fastsim.py` (cross-validates the native engine's house edges against the
Python engine within statistical error — **auto-skipped if the extension isn't
built**). When changing engine math, prefer adding a **directional** assertion
(e.g. "6:5 is worse than 3:2", "wider spread raises variance") over a brittle
exact number — and remember both engines must stay in agreement.

## CI/CD (`.github/workflows/ci.yml`)

- **test** — matrix over Python 3.9–3.12: `ruff check`, build the native engine
  (`setup.py build_ext --inplace`), then `pytest` (so `test_fastsim.py` runs).
- **build-dashboard** — rebuilds `dashboard.html` + PNGs from the tracked
  `results/*.json` and uploads them as a CI artifact.
- **deploy-pages** — on `main`, publishes the dashboard to GitHub Pages
  (`index.html`). One-time setup: repo **Settings → Pages → Source: GitHub Actions**.

`results/*.json` (sweep + betspread data) **is committed** as the source of
truth; the derived `dashboard.html` and `*.png` are git-ignored and regenerated
by `make dashboard` / CI.

## Conventions & gotchas

- Money is tracked in **units**; `--dollars-per-unit` converts only at the end.
- **House-edge sign:** positive = casino advantage, negative = player advantage.
- A `--seed` reproduces results exactly **only for a fixed `--cores`** (each
  worker gets `base_seed + worker_index`, so changing core count re-partitions
  the RNG streams; the expected value is unchanged). The same caveat applies
  per engine: `--engine c` and `--engine python` use different RNGs, so a seed
  reproduces within an engine, not across the two (EV agrees, draws don't).
- Single/double-deck use the 4–8 deck strategy chart (slight underestimate there).
- The counter plays **every round** (no Wonging) — a "bet spread" means flat
  1 unit through neutral/negative counts, ramping up as the true count rises.
- **CSM** (`Rules(csm=True)` / `--csm`) reshuffles every round, so the count
  resets and counting is dead — a counter collapses to flat basic strategy. The
  CSM's per-hand edge is a touch lower than the matching shoe (no cut-card
  effect); the casino wins via more hands/hour.
- **Windowed CSM** (`Rules(csm_buffer=N)` / `--csm-buffer 16`) models a real
  partial-reservoir machine: the `Shoe` deals from a pool = full shoe minus the
  last N cards (held in a rolling buffer), and `Shoe.true_count()` returns the
  buffer's Hi-Lo count. The `window_counter` strategy bets on it. Counting only
  wins by **Wonging** (set `min_bet=0`); see `docs/csm_counting.md` and
  `csm_counting.py`. The native engine implements this too — `csm` is `Rules`
  tuple index 14, `csm_buffer` index 15; the strat tuple gained `play_deviations`
  (index 10, distinguishes the deviation-playing counter from the basic-playing
  window counter). Keep `_build_c_rules`/`_build_c_strat` and the `_fastsim.pyx`
  unpacking in sync if these tuples change.
- No network needed at any point; the dashboard opens from `file://`.
