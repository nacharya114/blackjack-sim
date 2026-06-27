# Continuous shuffle machines (CSM)

A **continuous shuffle machine** feeds every round's discards back into the shoe
and reshuffles immediately, so each hand is effectively dealt from a **full
multi-deck shoe**. That one mechanical change makes a CSM a fundamentally
different game from a cut-card shoe — and the simulator models it with a single
rule flag.

```bash
# A named preset (6-deck CSM, matched to vegas_6deck_s17)
python run_sim.py single --preset csm_6deck_s17 --rounds 100000000 --cores 0 --engine c

# Or add --csm to any rule set (penetration is then ignored)
python run_sim.py single --strategy counter --decks 6 --s17 --das --ls --csm \
    --rounds 100000000 --cores 0 --engine c
```

## How it's modelled

`Rules(csm=True)` makes `game.play_round` (and the native engine) **reshuffle the
whole N-deck stack before every round**. Consequences, all of which fall straight
out of that:

- The running count is reset every round, so the **true count at every decision is
  ~0** — the count never builds.
- Each round is an independent draw from a full shoe, so the **penetration / cut
  card is irrelevant** (the flag ignores it).

This is the standard way CSM house edge is analysed. A real machine holds the
just-played cards in its reservoir for a beat instead of returning them
instantly, but that removes ~15 of 312 cards from one round's pool — a difference
in house edge far below the third decimal place, so we deal from the full stack.

**Validation.** A shoe set to reshuffle after ~1 round (`--penetration 0.02`) must
converge to the CSM, and it does — both land on the same fresh-shoe basic edge
within the confidence interval (see `tests/test_simulation.py` and the numbers
below). The native (Cython) and Python engines also agree on a CSM config in
`tests/test_fastsim.py`.

## Three results that define CSM play

All figures: 6D S17 DAS LS 3:2, basic strategy, native engine.

### 1. The optimal strategy is plain basic strategy

Because every hand comes off a full shoe, the EV-optimal play on a CSM is exactly
**multi-deck basic strategy** — the same chart the shoe game uses, with **no index
deviations** (there's no count to deviate on). That is precisely the **true-count-0
column of the dashboard's strategy chart**, and `validate_strategy.py` already
proves it is EV-optimal. There is no separate CSM strategy to learn.

### 2. Card counting is worthless

Run the *same* Hi-Lo counter (1→12 ramp + Illustrious 18) on a deep shoe and on a
CSM:

| Game | Counter house edge | Counter σ / round |
|------|--------------------|-------------------|
| 6D shoe, 85% pen | **−1.16%** (player edge) | ~3.5 |
| 6D CSM | **+0.35%** (= flat basic) | ~1.1 |

On the CSM the bet ramp never engages (true count stays ~0, so every bet is the
1-unit minimum) and the index plays never trigger. The counter collapses into a
flat basic-strategy player — identical EV, and the low ~1.1 σ/round confirms the
spread is gone. **Counting a CSM cannot beat it.**

### 3. The casino's real edge is hands per hour

A CSM's *per-hand* edge is actually a hair **lower** than the matching cut-card
shoe, because it removes the **cut-card effect** (in a cut-card game you deal
slightly more rounds out of ten-poor, house-favourable shoes, which nudges the
shoe's edge up):

| Game | Per-hand HE | Hands/hr | Hourly EV @ $10/unit |
|------|-------------|----------|----------------------|
| 6D shoe (pen 0.75) | +0.370% | 100 | −$3.70 |
| 6D CSM | +0.348% | 130 | **−$4.53** |

Cut-card effect ≈ **+0.02%** in the shoe's favour — small, and squarely in line
with Griffin's classic estimate. But the CSM never stops to shuffle, so it deals
~30% more hands per hour. The net for the player is **~22% more money lost per
hour** despite the lower per-hand edge. That speed-up, not the per-hand edge, is
why the house likes them.

## Takeaways

- Play **basic strategy**; don't bother counting or spreading bets.
- A CSM is marginally *better* per hand than a deep-pen shoe of the same rules,
  but you'll play far more hands per hour — so it costs you more overall.
- For an advantage player, a CSM table is simply unbeatable; look for a hand-shuffled
  or cut-card shoe instead.
