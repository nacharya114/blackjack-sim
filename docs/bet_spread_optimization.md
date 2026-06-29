# Optimizing the bet spread: EV-max vs risk-adjusted, under a table maximum

`docs/bet_spreads.md` asks the *breakeven* question — how wide must a simple
1-to-N linear ramp be to stop losing money. This note asks the **optimization**
question: given a realistic **table maximum** and the rule that we **don't put
money out in deeply negative counts**, what ramp *shape* is best — and "best"
under two different objectives that pull in opposite directions.

Tool: `optimize_betspread.py`. Engine: native Cython. Game: **6D, S17, DAS, late
surrender, 3:2**, Hi-Lo with the Illustrious-18 index plays. Penetration 0.75
(1.5 decks cut — a common Strip 6-decker). $10/unit, 100 hands/hr, 60M rounds per
finalist, common random numbers (one shared seed) so ramp *shape*, not RNG noise,
drives every comparison.

## Three modeling choices that make this realistic

1. **Sit out deep-negative counts (Wong out).** Below true count **−1** the bet
   is **0** — you don't play the hand. From −1 through the neutral counts you keep
   the **table minimum** (1 unit) as cover, then ramp. This single change is the
   biggest lever in the whole study (see below).
2. **Honor a table maximum.** Every bet is clamped to `[1, cap]` units, so the
   cap *is* max-bet ÷ table-minimum: a \$25–\$500 table is **cap = 20**. We sweep
   **1-12, 1-20, 1-40**.
3. **Be more aggressive at higher counts, tunably.** The ramp shape is a power
   curve with exponent `gamma`: `gamma<1` jumps toward the cap early, `gamma=1`
   is linear in true count (≈ Kelly, since advantage is ≈ linear in TC), `gamma>1`
   holds small bets through marginal counts and saves the big bets for the
   richest shoes. The optimizer grids `gamma`, the ramp-start count, and the
   top-out count for each cap.

## The two objectives

- **EV-max** — highest raw EV/round (\$/hr). Under a hard cap this is close to
  *bang-bang*: bet the table minimum (or sit out) until the count turns
  advantageous, then **slam the cap**. Most money, most variance.
- **SCORE-max** — highest **SCORE = (EV/σ)²·10⁶**, the bankroll-growth metric
  counters actually optimize (proportional to Kelly log-growth and to `1/N0`).
  SCORE is **scale-invariant**, so the cap matters only through the *shape* it
  permits: a wider table lets you concentrate more action into the very highest
  counts, which is where edge-per-unit-variance is greatest.

## Result — the Wong-out lever (reference spreads)

Same positive ramp (the standard 1-8 linear), the only change is what you do in
negative counts:

| spread | EV/round | \$/100 hr | SCORE |
|--------|---------:|----------:|------:|
| flat 1 unit | −0.0034 | −\$3.38 | 0 |
| 1-8 linear, **play through** negatives at 1u | +0.0081 | +\$8.10 | 11.7 |
| 1-8 linear, **Wong out** below TC −1 | **+0.0133** | **+\$13.31** | **34.0** |

**Not betting into deep-negative counts raises \$/hr by ~64% and nearly triples
SCORE — for free.** It is a bigger win than any amount of ramp-shape tuning
below. (Caveat: the engine still counts sat-out hands as rounds at the table, so
this *understates* a back-counter who table-hops to play only good shoes.)

## Result — optimized spreads by table maximum (pen 0.75)

| cap | objective | ramp (TC:bet) | EV/round | σ/round | \$/100 hr | SCORE | N0 |
|-----|-----------|---------------|---------:|--------:|----------:|------:|---:|
| 1-12 | EV-max    | 1·1·1·**9·11·12** (TC −1…4) | +0.0276 | 4.63 | +\$27.6 | 35.5 | 0.03 M |
| 1-12 | SCORE-max | 1·1·1·4·6·9·12 (TC −1…5)    | +0.0200 | 3.23 | +\$20.0 | **38.3** | 0.03 M |
| 1-20 | EV-max    | 1·1·1·**15·18·20** (−1…4)   | +0.0463 | 7.59 | +\$46.3 | 37.2 | 0.03 M |
| 1-20 | SCORE-max | 1·1·1·5·9·12·16·20 (−1…6)   | +0.0295 | 4.58 | +\$29.5 | **41.4** | 0.02 M |
| 1-40 | EV-max    | 1·1·1·**31·36·40** (−1…4)   | +0.0939 | 15.27 | +\$93.9 | 37.8 | 0.03 M |
| 1-40 | SCORE-max | 1·1·1·9·17·24·32·40 (−1…6)  | +0.0584 | 8.85 | +\$58.4 | **43.6** | 0.02 M |

(The leading `1·1·1` is the cover bet at TC −1, 0, +1; below −1 the bet is 0.)

## How to read it

- **EV-max just levers up.** Doubling the cap (12→20→40) nearly doubles \$/hr
  (28→46→94) — but σ doubles right alongside it (4.6→7.6→15.3), so **SCORE barely
  moves** (~36–38). Pure EV-max buys you raw expectation by taking proportionally
  more variance (and more "heat"): it slams the cap at TC +2 and tops out by +4.
  It is the right spread only if your bankroll is large relative to the cap and
  you don't fear ruin or a back-off — e.g. a short, well-financed session.
- **SCORE-max ramps gradually and back-loads.** It keeps the table minimum
  through the marginal counts, then climbs to the cap only at the very top
  (TC +5/+6, `gamma`≈1.5–2). It earns ~35% less \$/hr than EV-max at the same cap
  but at far lower σ — and its **SCORE rises with the cap** (38→41→44), because a
  wider table lets it push more of its action into the richest, most
  edge-efficient counts. This is the bankroll-growth-optimal spread.
- **A wider table helps both objectives, differently.** EV-max turns it into raw
  \$/hr at constant risk-efficiency; SCORE-max turns it into genuine
  risk-efficiency (lower N0, faster compounding) at more modest \$/hr.
- **The hierarchy of levers:** *Wong out* (≈3× SCORE) ≫ *table cap / how wide you
  can ramp* ≫ *the exact ramp curve*. Get the first two right before fine-tuning
  `gamma`.

## Penetration scales the edge, not the shape (pen 0.85)

Re-running at **0.85 penetration** (1 deck cut — a good game) leaves the *optimal
ramp shapes essentially unchanged* but roughly **doubles SCORE** and lifts \$/hr
sharply — penetration multiplies the whole opportunity:

| cap | objective | \$/100 hr | SCORE | vs pen 0.75 SCORE |
|-----|-----------|----------:|------:|------------------:|
| 1-20 | EV-max    | +\$63.2 | 57.8 | 37.2 |
| 1-20 | SCORE-max | +\$44.4 | **66.7** | 41.4 |
| 1-40 | SCORE-max | +\$88.0 | **68.7** | 43.6 |

The Wong-out lever is even larger here (play-through SCORE 26.2 → Wong 57.8). The
takeaway is unchanged: **deeper penetration is worth more than a wider table**,
and the optimal *shape* (cover to +1, smooth ramp to the cap at +5/+6, sit out
below −1) is robust across penetration — only its payoff scales.

## Practical recommendation

For most counters — finite bankroll, wanting to actually compound and not get
backed off — take the **SCORE-max** spread for your table's maximum: cover the
minimum through TC +1, then ramp smoothly (`gamma`≈1.5–2) to the cap at TC +5/+6,
and **sit out everything below TC −1**. Reserve the EV-max "slam the cap at +2"
shape for short, deep-bankroll, low-heat-tolerance situations where maximizing
this session's expectation outweighs long-run ruin risk.

## Reproduce

```bash
python optimize_betspread.py --pen 0.75 --caps 12 20 40 \
    --rounds 12000000 --rounds-final 60000000 --cores 0 \
    --out results/betspread_opt_pen75.json

# Drop one optimized spread straight into the CLI to re-check it:
python run_sim.py single --strategy counter --decks 6 --s17 --das --ls \
    --penetration 0.75 --min-bet 0 \
    --bet-ramp "-1:1,0:1,1:1,2:5,3:9,4:12,5:16,6:20" --rounds 60000000 --cores 0
```

## Caveats

- **Sat-out hands still count as rounds here**, so EV/round, \$/hr and SCORE are
  conservative for a true back-counter who only sits at the table for good shoes.
  The spread-vs-spread comparison is apples-to-apples; the absolute \$/hr is a floor.
- SCORE/N0 assume bets small relative to bankroll (the Kelly regime). Exact
  breakeven still carries ≈100% long-run risk of ruin — see `docs/bet_spreads.md`.
- Single/double-deck use the 4–8 deck chart (a slight underestimate there).
- Monte-Carlo estimates — quote with the 95% CI; near-tied SCOREs are bracketed,
  not pinned to a decimal.
