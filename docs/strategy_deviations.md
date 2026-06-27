# Strategy deviations & per-action EVs

The card counter plays **basic strategy modified by Hi-Lo index plays** — the
"Illustrious 18" plus insurance. A deviation says: *play the basic action until
the true count reaches an index number, then switch.* This note explains the
dashboard's **Card-counter strategy** panel and how the numbers are produced.

## Two views

- **Deviations** — the index table straight from the engine
  (`blackjack/strategy.py:ILLUSTRIOUS_18` + insurance): for each hand, the basic
  play, the index true count, the action to switch to, and on which side of the
  index it applies.
- **Action EVs** — for a chosen hand, the EV of each action (stand / hit /
  double / surrender) as a function of the true count. The two competing curves
  (basic action vs. deviation) are drawn solid; dominated actions are dimmed.
  **The true count where the basic and deviation curves cross *is* the index.**

## How the EVs are computed

`blackjack/evcalc.py` computes total-dependent EVs on a **count-adjusted infinite
deck** (no simulation, so the curves are smooth and exact for the model):

- **Composition model.** A Hi-Lo true count `tc` means the expected Hi-Lo tag of
  the next card is exactly `-tc/52` — an identity, independent of decks
  remaining (seen cards sum to `+RC`, unseen to `-RC`, and `-RC/(52·decks)` =
  `-tc/52`). We realise it by moving mass `m = tc/104` from the low group (2–6)
  to tens & aces:

  ```
  p(2..6) = 1/13 − m/5     p(7..9) = 1/13
  p(ten)  = 4/13 + 0.8·m   p(ace)  = 1/13 + 0.2·m
  ```

- **Dealer** outcomes come from a recursion (S17/H17 aware), excluding a dealer
  natural when the rules peek — i.e. the situations where the player still acts.
- **Player** EVs: `stand` vs. the dealer distribution; `hit` plays optimally
  thereafter; `double` = 2 × (one card, then stand); `surrender` = −0.5.
- **Insurance** is a count-only bet: EV per unit = `3·p(ten) − 1`, which turns
  positive once tens exceed 1/3 of the deck (≈ true count +3).

Generate the panel data with:

```bash
python strategy_ev.py --out results/strategy_ev.json     # add --h17 for an H17 game
```

## Self-validation (and a bug it caught)

`strategy_ev.py` prints, for every index play, the analytic EV crossover next to
the engine's published index — they agree within ~1 true count across the board
(e.g. 16v10 → +0.08 vs index 0; 12v3 → +1.33 vs +2; insurance → +3.33 vs +3).
`tests/test_evcalc.py` asserts this automatically.

This cross-check surfaced a real bug: **13 vs 2 and 12 vs 4** were originally
encoded as *stand-over-stand* — the deviation action matched the basic action,
so the index play could never change anything (a no-op). They are hands you
*stand* by basic but should *hit* at deeply negative counts, like 12v5 / 12v6 /
13v3. Fixed in `ILLUSTRIOUS_18`, with `tests/test_engine_units.py`
(`test_no_deviation_is_a_noop`) guarding against a regression.

## Validating the tables (`validate_strategy.py`)

The chart stays the engine's runtime default; `validate_strategy.py` is an
offline cross-check that proves it is EV-correct (and a CI guard via
`tests/test_strategy_validation.py`). Run `make validate`. It:

1. checks all **170 hard basic-strategy cells** against the EV-optimal action on
   a neutral deck, under both S17 and H17 (170/170 exact, no mismatches);
2. **derives** the Hi-Lo index for every borderline hard hand by scanning the
   true count and diffs the result against `ILLUSTRIOUS_18` (all 15 confirmed);
3. lists derived hard deviations *outside* the Illustrious 18 (e.g. 8v6 double
   at +1.6, 16v7 stand at +7.7) — confirming the I18 is a sensible curated
   subset rather than the complete set.

It exits non-zero on any disagreement beyond a small EV tie-tolerance. Scope is
hard totals (soft hands and pairs need the EV-calculator extensions noted above).

> Note: the committed `results/sweep.json` / `betspread_*.json` were generated
> before that fix; the effect on the counter's overall EV is negligible (these
> two deviations fire only at rare deeply-negative counts), but regenerate with
> `make sweep` / `make betspread` if you want them exactly current.
