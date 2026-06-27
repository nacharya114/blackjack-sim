# Blackjack EV / House-Edge Simulator

A self-contained Monte-Carlo blackjack simulator that measures the **house edge**
and **hourly EV** of different strategies, rule sets, and side bets — plus an
**offline HTML dashboard** to explore the results and project sessions. Inspired
by [AttackingOrDefending/Blackjack-Strategy-Simulator](https://github.com/AttackingOrDefending/Blackjack-Strategy-Simulator),
rebuilt from scratch with side-bet support added.

Everything runs **locally with no internet connection**. The engine is pure
Python standard library; `matplotlib` is only needed for the optional static
PNG charts. An **optional Cython "native" engine** compiles the round loop to
multithreaded C for a ~100–150× speedup (see
[Going fast](#going-fast-the-native-engine)); without it everything falls back to
pure Python.

---

## Quick start

```bash
# 1. Run the full comparison matrix on ALL cores (--cores 0 = use every core)
python run_sim.py sweep --rounds 10000000 --cores 0 --seed 12345

# 2. Build the interactive dashboard + PNG charts from those results
python visualize.py --data results/sweep.json

# 3. Open results/dashboard.html in any browser (double-click — works offline)
```

`--cores 0` auto-detects every logical core on the machine; pass an explicit
number (e.g. `--cores 8`) to cap it. More rounds = a tighter confidence
interval on the EV. At 10,000,000 rounds the 95% CI on the per-round house edge
is roughly ±0.07% (vs ±0.22% at 1M).

### How many rounds do I need?

| Rounds/config | 95% CI on house edge | Python, 1 core | Native, 1 core |
|---------------|----------------------|----------------|----------------|
| 1,000,000     | ±0.22%               | ~12 s          | ~0.08 s        |
| 10,000,000    | ±0.07%               | ~115 s         | ~0.75 s        |
| 100,000,000   | ±0.022%              | ~19 min        | ~7.5 s         |
| 1,000,000,000 | ±0.007%              | ~3 h           | ~75 s          |

The error shrinks with the square root of the rounds, and wall-clock time falls
roughly linearly with the number of cores. The pure-Python engine does a 10M-round
sweep in a few minutes on a laptop; the [native engine](#going-fast-the-native-engine)
makes 100M+ rounds — and a ±0.02% CI — routine in seconds.

---

## Running a single configuration

```bash
# A named preset
python run_sim.py single --strategy basic --preset vegas_6deck_s17 --rounds 1000000

# Build a rule set from flags
python run_sim.py single --strategy basic \
    --decks 6 --h17 --no-das --no-ls --payout 1.2 --rounds 1000000

# A Hi-Lo card counter on a deeply-dealt shoe, with a 1–12 bet spread at $25/unit
python run_sim.py single --strategy counter \
    --decks 6 --s17 --penetration 0.85 --dollars-per-unit 25 --rounds 1000000

# Add side bets (flat 1-unit side wager every round)
python run_sim.py single --strategy basic --preset vegas_6deck_s17 \
    --sidebets perfect_pairs 21+3 --side-bet-unit 1 --rounds 1000000

# Load a rules file and write the result JSON out
python run_sim.py single --strategy basic --config configs/vegas_6deck.json \
    --rounds 1000000 --out results/my_run.json
```

Key flags: `--s17/--h17` (dealer stands/hits soft 17), `--das/--no-das`
(double after split), `--ls/--no-ls` (late surrender), `--payout` (1.5 = 3:2,
1.2 = 6:5), `--penetration` (fraction of shoe dealt before reshuffle),
`--dollars-per-unit`, `--hands-per-hour`, `--cores`, `--seed`.

Presets available: `vegas_6deck_s17`, `vegas_6deck_h17`, `downtown_2deck_h17`,
`single_deck_s17`, `bad_6deck_65`.

---

## Going fast: the native engine

The simulator includes an **optional Cython engine** that ports the entire
round loop to `nogil` C. It runs ~13M rounds/s per core (vs ~0.09M for pure
Python) and uses real threads across cores, so 100M+ round runs — and the tight
confidence intervals that come with them — take seconds instead of minutes.

```bash
pip install cython      # or: make dev
make build              # python setup.py build_ext --inplace  (needs a C compiler)

# --engine {auto,python,c}; "auto" (the default) uses the native engine if built
python run_sim.py single --strategy basic --preset vegas_6deck_s17 \
    --rounds 200000000 --cores 0 --engine c
python run_sim.py sweep --rounds 100000000 --cores 0 --engine c
```

If you never build it, nothing changes — the simulator runs on pure Python as
before. The native engine is a faithful port (it shares the strategy tables from
`blackjack/strategy.py`, so there is one source of truth) and is cross-validated
against the Python engine in `tests/test_fastsim.py`. It uses a different RNG, so
it matches the Python engine's **expected values** but not its exact card draws
for a given seed. Full details: [`docs/fast_engine.md`](docs/fast_engine.md).

---

## The dashboard

`results/dashboard.html` is one self-contained file with the sweep data inlined,
so it opens straight from disk — no local server, no CORS issues. It gives you:

- A **sortable table** of every run (house edge, element of risk, EV/round,
  hourly EV, hourly σ).
- **Bar charts** of house edge and hourly EV across configurations.
- A **selected-run panel** with full stats and side-bet breakdown.
- An interactive **session Monte-Carlo projection**: adjust `$/unit`,
  `hands/hour`, `session hours`, and `bankroll`, and it random-walks 24 sample
  sessions (using each run's per-round mean and σ), flags ruin, and shows the
  EV line.
- A **bet-spread breakeven panel** (shown when `results/betspread_*.json`
  exist): a dual-axis chart of house edge and per-round volatility vs. the
  counter's bet spread, with the breakeven crossover marked and a penetration
  toggle. Generate the data with `betspread.py`; details in `docs/bet_spreads.md`.
- A **card-counter strategy panel** (shown when `results/strategy_ev.json`
  exists): a toggle between the Hi-Lo **deviation table** (Illustrious 18 +
  insurance) and **per-action EV curves** vs. true count, where the basic and
  deviation curves cross at the index. Generate with `strategy_ev.py`; details
  in `docs/strategy_deviations.md`.
- A **file picker** to load any other `sweep.json` you generate.

`visualize.py` also writes three static PNGs (`house_edge_by_ruleset.png`,
`hourly_ev.png`, `sidebet_house_edge.png`) for reports/slides. Pass `--no-png`
to skip them.

---

## Validation

House edges from the included **10M-round** sweep (seed 12345, single-core in
the build environment; reproduce on your machine with `--cores 0`), with
published basic-strategy figures for comparison. 95% CI is ±0.07% per basic run
at 10M rounds.

| Configuration            | Strategy | Measured HE | Published ≈ |
|--------------------------|----------|-------------|-------------|
| 6D S17 DAS LS 3:2        | basic    | +0.365%     | 0.36–0.46%  |
| 6D H17 DAS noLS 3:2      | basic    | +0.640%     | 0.62–0.66%  |
| 2D H17 DAS 3:2           | basic    | +0.439%     | 0.40–0.46%  |
| 1D S17 noDAS 3:2         | basic    | +0.154%     | see note ↓  |
| 6D H17 noDAS 6:5         | basic    | +2.140%     | ~2.0–2.3%   |
| 6D S17 (deep) counter    | counter  | **−1.061%** | player edge |

Card counting flips the edge to the player on a deeply-dealt good game, as it
should. (Positive house edge = casino advantage; negative = player advantage.
95% confidence intervals are ±0.22% per run at 1M rounds, wider for the counter
because of the bet spread.)

### Side bets (6 decks)

| Side bet                   | Paytable                         | House edge |
|----------------------------|----------------------------------|------------|
| Perfect Pairs              | perfect 25 / colored 12 / mixed 6 | ~6.1%      |
| 21+3                       | suited-trips 100 / SF 40 / trips 30 / straight 10 / flush 5 | ~4.5% |

Side-bet edges depend heavily on the paytable and deck count; the constants live
in `blackjack/sidebets.py` if you want to model a different table.

---

## Known limitations / notes

- **Single & double deck use the 4–8 deck basic-strategy chart.** Truly optimal
  single-deck play differs on a handful of hands, so the 1-deck number is a
  slight underestimate of the real basic-strategy edge for that game. The
  multi-deck numbers (where most play happens) are the well-validated ones.
- The counter uses **Hi-Lo with the Illustrious-18 deviations** and a simple
  true-count bet ramp; it is a realistic-but-not-maximal counter, not a
  theoretical-max EV bot.
- All money is tracked internally in **units** (multiples of the base bet) and
  converted to dollars only at the end via `--dollars-per-unit`, so you can
  re-stake any result without re-simulating.
- Results are Monte-Carlo estimates: quote them with their confidence interval,
  and increase `--rounds` for more precision.
- **Reproducibility:** a given `--seed` reproduces results exactly *for a fixed
  core count*. Each worker gets seed `base_seed + worker_index`, so changing
  `--cores` re-partitions the random streams and shifts the estimate within its
  confidence interval (the expected value is unchanged). Fix both `--seed` and
  `--cores` if you need bit-identical runs.

---

## Project layout

```
blackjack-sim/
├── run_sim.py              # CLI: `single` and `sweep` (single takes --bet-ramp)
├── visualize.py            # builds dashboard.html + PNG charts from a sweep
├── betspread.py            # bet-spread breakeven search for a card counter
├── strategy_ev.py          # Hi-Lo deviation + action-EV data for the strategy panel
├── dashboard_template.html # offline dashboard template (data inlined at build)
├── setup.py                # builds the optional native (Cython) engine
├── requirements.txt        # matplotlib (only for the PNGs)
├── configs/
│   └── vegas_6deck.json    # example rules file
├── docs/
│   ├── bet_spreads.md          # lowest-risk breakeven spread analysis
│   ├── strategy_deviations.md  # deviations + per-action EV methodology
│   └── fast_engine.md          # the optional native (Cython) engine
├── results/                # sweep.json, betspread_*.json, strategy_ev.json, dashboard.html, PNGs
└── blackjack/              # the engine
    ├── cards.py            # card encoding, hand totals, blackjack check
    ├── rules.py            # Rules dataclass + presets
    ├── shoe.py             # shoe, dealing, running/true count
    ├── strategy.py         # basic-strategy tables + Hi-Lo deviations
    ├── evcalc.py           # analytic count-adjusted per-action EV calculator
    ├── sidebets.py         # Perfect Pairs & 21+3 paytables and resolution
    ├── players.py          # BasicStrategy / CardCounter bettors
    ├── game.py             # one full round (splits, doubles, surrender, dealer)
    ├── _fastsim.pyx        # optional native (Cython) port of the round loop
    └── simulator.py        # multi-core Monte-Carlo driver + statistics
```

## Requirements

Python 3.9+. The engine needs **nothing but the standard library**. For the
optional PNG charts: `pip install -r requirements.txt`. For the optional native
engine: a C compiler plus `pip install cython`, then `make build`.

## Development & CI

```bash
make dev       # install dev tooling (pytest, ruff, cython, matplotlib)
make build     # compile the optional native engine in place
make test      # run the test suite (~15s; small fixed-seed sims + unit tests)
make lint      # ruff check .
make validate  # cross-check the strategy tables against the EV calculator
make help      # list all targets
```

GitHub Actions (`.github/workflows/ci.yml`) runs lint, builds the native engine,
and runs tests (including the native-vs-Python cross-check) across Python
3.9–3.12 on every push/PR, rebuilds the dashboard from the tracked
`results/*.json`, and (on `main`) deploys it to **GitHub Pages** — enable it
once under *Settings → Pages → Source: GitHub Actions*. The generated
`dashboard.html`/PNGs are git-ignored and regenerated by `make dashboard`; the
simulation data (`results/*.json`) is the committed source of truth.
