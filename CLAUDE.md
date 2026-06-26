# CLAUDE.md — project guide for Claude Code

Monte-Carlo blackjack simulator that measures **house edge / EV** across
strategies, rule sets, side bets, and card-counter **bet spreads**, plus an
offline HTML dashboard. The engine is **pure Python standard library** (no
runtime dependencies); `matplotlib` is used solely for optional PNG charts.

## Run it

```bash
# Full comparison matrix on all cores, then build the dashboard
python run_sim.py sweep --rounds 10000000 --cores 0 --seed 12345
python visualize.py --data results/sweep.json
# Open results/dashboard.html (offline, just double-click)

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
  - `shoe.py` — `Shoe`: shuffled multi-deck stack, cut card, Hi-Lo running/true count.
  - `strategy.py` — basic-strategy tables + Hi-Lo index deviations (Illustrious 18).
  - `players.py` — `BasicStrategy` (flat) and `CardCounter` (`bet_ramp` → bet by
    floor(true count); plays index deviations + insurance). `build_strategy()` factory.
  - `sidebets.py` — Perfect Pairs & 21+3 paytables and resolution.
  - `game.py` — one full round: splits, doubles, surrender, dealer play, payout.
  - `simulator.py` — multiprocessing Monte-Carlo driver + EV/variance statistics.
- `run_sim.py` — CLI (`single`, `sweep`). `build_sweep_matrix()` defines the
  dashboard matrix in one place. `single` accepts `--bet-ramp` / `--min-bet`.
- `betspread.py` — sweeps a counter's bet spread to find the lowest-risk
  breakeven spread (see `docs/bet_spreads.md`).
- `blackjack/evcalc.py` + `strategy_ev.py` — analytic count-adjusted per-action
  EV calculator and the generator for the dashboard's strategy-deviation panel;
  self-validates EV crossovers against the engine indices (see
  `docs/strategy_deviations.md`).
- `visualize.py` — builds `dashboard.html` (sweep data + any `betspread_*.json`
  + `strategy_ev.json` inlined at `/*__…__*/` markers) and optional PNGs.
- `_run_chunk.py` — internal helper to compute a slice of the sweep resumably.
- `configs/` example rules JSON · `docs/` analysis writeups · `results/`
  generated data + dashboard.

## Development

```bash
make dev      # pip install -r requirements-dev.txt  (pytest, ruff, matplotlib)
make test     # pytest  (fast: small fixed-seed sims + unit tests, ~15s)
make lint     # ruff check .
```

Tests live in `tests/`: `test_engine_units.py` (cards/rules/shoe/sidebets,
deterministic), `test_simulation.py` (small fixed-seed integration runs asserting
magnitude bands + directional invariants — robust to Monte-Carlo noise),
`test_tools.py` (betspread math + dashboard inlining; no matplotlib needed).
When changing engine math, prefer adding a **directional** assertion (e.g. "6:5
is worse than 3:2", "wider spread raises variance") over a brittle exact number.

## CI/CD (`.github/workflows/ci.yml`)

- **test** — matrix over Python 3.9–3.12: `ruff check` + `pytest`.
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
  the RNG streams; the expected value is unchanged).
- Single/double-deck use the 4–8 deck strategy chart (slight underestimate there).
- The counter plays **every round** (no Wonging) — a "bet spread" means flat
  1 unit through neutral/negative counts, ramping up as the true count rises.
- No network needed at any point; the dashboard opens from `file://`.
