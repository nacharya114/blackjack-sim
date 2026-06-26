# Bet spreads: the lowest-risk breakeven spread (6D S17 DAS, no surrender)

**Question.** Playing every round (no Wonging) with a Hi-Lo count and the
Illustrious-18 index plays, how wide must a counter spread their bets to stop
losing money in a 6-deck, S17, DAS, no-surrender, 3:2 game — and what is the
*lowest-risk* spread that gets there?

## How a "spread" is defined here

A spread is parametrised by one number, its **top bet `N`**. You flat-bet
**1 unit** through true counts ≤ +1 and ramp linearly up to **`N` units** at
true count ≥ **+5**, where the bet tops out (`betspread.py`, `build_ramp`). So a
"1-to-4 spread" bets 1 unit off the top and 4 units when the count is good.
Integer rounding makes the intermediate steps slightly lumpy:

| spread | TC≤1 | +2 | +3 | +4 | +5 |
|--------|------|----|----|----|----|
| 1-3    | 1    | 2  | 2  | 2  | 3  |
| 1-4    | 1    | 2  | 2  | 3  | 4  |
| 1-5    | 1    | 2  | 3  | 4  | 5  |

The counter **plays through negative counts at 1 unit** — it does not sit them
out. Back-counting / Wonging would beat these numbers comfortably; this is the
"sit and grind a full spread" case.

## Result

12M rounds per point, seed 12345, $10/unit, 100 hands/hr. `+HE` = house edge,
`−HE` = player edge; 95% CI shown.

**Penetration 0.75 (1.5 decks cut off — a common Strip 6-decker)**

| spread | total HE | EV/round | std/round | $/100 hr | N0 (rounds) |
|--------|---------:|---------:|----------:|---------:|------------:|
| 1-1 (flat) | +0.343% | −0.0039 | 1.16 | −$3.90 | — |
| 1-2 | +0.183% | −0.0023 | 1.29 | −$2.25 | — |
| 1-3 | +0.069% | −0.0009 | 1.44 | −$0.92 | — |
| **1-4** | **−0.020%** | **+0.0003** | **1.56** | **+$0.28** | 31.4 M |
| 1-5 | −0.130% | +0.0019 | 1.78 | +$1.93 | 0.85 M |
| 1-6 | −0.227% | +0.0036 | 2.02 | +$3.58 | 0.32 M |
| 1-8 | −0.353% | +0.0061 | 2.39 | +$6.11 | 0.15 M |

Breakeven (house edge crosses zero) ≈ **1-to-3.8**, i.e. a **1-to-4 spread**.

**Penetration 0.833 (1 deck cut off — a good game)**

| spread | total HE | EV/round | std/round | $/100 hr | N0 (rounds) |
|--------|---------:|---------:|----------:|---------:|------------:|
| 1-1 (flat) | +0.326% | −0.0037 | 1.16 | −$3.72 | — |
| 1-2 | +0.114% | −0.0014 | 1.32 | −$1.44 | — |
| **1-3** | **−0.037%** | **+0.0005** | **1.49** | **+$0.51** | 8.5 M |
| 1-4 | −0.162% | +0.0023 | 1.65 | +$2.34 | 0.50 M |
| 1-5 | −0.296% | +0.0046 | 1.91 | +$4.62 | 0.17 M |
| 1-6 | −0.411% | +0.0069 | 2.19 | +$6.90 | 0.10 M |
| 1-8 | −0.572% | +0.0107 | 2.64 | +$10.67 | 0.06 M |

Breakeven ≈ **1-to-2.8**, i.e. a **1-to-3 spread**.

## Reading it as "lowest risk"

- **Risk = volatility rises monotonically with the spread** (std/round and the
  hourly σ both climb as `N` grows). So among spreads that are *at least*
  breakeven, the **smallest one is the least volatile** — that is the
  lowest-risk breakeven spread: **1-4 at standard penetration, 1-3 at deep
  penetration.**
- **Exact breakeven is not "safe."** At EV ≈ 0 you still carry full variance but
  no edge, so the long-run risk of ruin is ≈ 100% — `N0 = (σ/EV)²` blows up
  (31 M rounds at the 1-4 breakeven point). To actually protect a bankroll you
  need EV **> 0**, which means going **one notch wider** (1-5 / 1-4) and sizing
  the bankroll to the σ. Widening the spread lowers `N0` (more risk-*efficient*)
  even though absolute variance grows.
- **Penetration is the dominant lever**, not rule tweaks: going from 1.5 to 1.0
  decks cut shaves a full unit off the breakeven top bet. If you can only get a
  shallow shoe, you need a wider (riskier) spread just to break even.

## Caveats

- Breakeven depends on where the ramp tops out (`TOP_TC`, default +5). A ramp
  that maxes at +4 needs a slightly larger top bet; one that maxes at +6 a
  slightly smaller one.
- These assume the engine's Hi-Lo + Illustrious-18 counter (a realistic, not
  theoretical-max, player) and no Wonging.
- Monte-Carlo estimates — quote with the 95% CI. Near breakeven the edge is tiny
  relative to σ, so the crossover is bracketed (1-3 vs 1-4), not pinned to a
  decimal.

## Reproduce

```bash
python betspread.py --pen 0.75  --rounds 12000000 --cores 0 --out results/betspread_pen75.json
python betspread.py --pen 0.833 --rounds 12000000 --cores 0 --out results/betspread_pen83.json

# Test one custom spread directly through the CLI:
python run_sim.py single --strategy counter --decks 6 --s17 --das --no-ls \
    --penetration 0.75 --bet-ramp "1:1,2:2,3:2,4:3,5:4" --rounds 12000000 --cores 0
```
