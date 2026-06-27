# Counting a *windowed* CSM (partial reservoir)

The [idealised CSM](csm.md) reshuffles every card back instantly, so the count
never builds and counting is dead. A **real** continuous shuffler doesn't — it
holds a buffer of recently played cards out of the dealing pool for a beat. That
opens a small loophole, analysed by
[discountgambling.net](https://discountgambling.net/2012/07/27/counting-csm-blackjack-ev/),
which this simulator reproduces.

## The model (`Rules(csm_buffer=N)`)

A windowed CSM deals each card uniformly from **the full shoe minus the last `N`
cards dealt** — those `N` cards sit in the machine's chute/buffer. Following the
post, the primary analysis uses:

- **6 decks**, a **16-card buffer** (a one2six holds roughly this much),
- a plain **Hi-Lo count of the buffer**, used **directly** as the bet signal (no
  true-count divisor — the buffer is a fixed size).

Because the buffer's `N` cards are out of the pool, the pool's Hi-Lo sum is the
*negative* of the buffer count. So a **high buffer count** (lots of low cards
parked in the chute) means the pool is **ten-rich right now** — a briefly +EV
hand. `blackjack/shoe.py` implements this as an O(1) draw-from-pool with a
rolling buffer; `Shoe.true_count()` returns the buffer count in this mode, and a
`window_counter` strategy bets up on it.

## It matches the post

Flat-betting basic strategy and binning each hand by the buffer count at the
start of the round (6D S17 DAS LS, 16-card buffer) gives a per-count EV that
**rises monotonically with the buffer count** and crosses zero in the +3…+5
region:

| Buffer count | ~Frequency | Flat EV (trend) |
|--------------|-----------|-----------------|
| ≤ +2 | ~70% | negative (−0.2% to −0.5%) |
| +3 … +4 | ~13% | crossing zero |
| +5 … +7 | ~7% | small positive |
| ≥ +8 | ~1% | strongly positive (≈ +1% and up) |

- **P(buffer count ≥ +5) = 8.2%** — exactly the post's figure (measured 8.15%).
- Those hands are +EV and the edge climbs steeply with the count, just as the
  post reports (it quotes ≈ +0.04% right at +5). Individual high-count bins are
  noisy even at 100M rounds because they're rare; run `csm_counting.py` for the
  full curve with your own sample size.

The signal is real but **thin**: it is only worth a meaningful amount on the ~1%
of hands at +8 or higher, and the favourable composition evaporates within a few
hands as the buffer cycles.

## Does counting actually win?

The `window_counter` bets off the buffer count. Two regimes, measured at **200M
rounds on the native engine** (6D S17 DAS LS, 16-card buffer; *edge* = player
advantage per unit wagered, positive = player):

| Strategy | Edge / unit | Hands played |
|----------|-------------|--------------|
| Flat basic strategy | **−0.313%** (house) | 100% |
| Bet-spread 1–12, bet up at +5 | −0.169% (house) | 100% |
| Bet-spread 1–20, bet up at +5 | −0.085% (house) | 100% |
| **Wong: only play buffer ≥ +5** | **+0.131%** (player) | 8.2% |
| **Wong + spread, ≥ +5** | **+0.212%** (player) | ~18% |
| **Wong: only play buffer ≥ +8** | **+0.259%** (player) | ~1.1% |

The result is sharp:

- **Bet-spreading alone does not beat it.** Ramping the bet on the buffer count
  roughly *halves* the house edge (−0.31% → −0.09% per unit with a 1–20 spread)
  but never crosses zero — you still have to play the ~92% of hands that are
  below +5, and they're losers.
- **Wonging does.** If you only sit down (and bet) when the buffer count is high,
  the edge flips positive: ~**+0.13%** per unit playing everything ≥ +5, up to
  ~**+0.26%** playing only the rare ≥ +8 hands. That is the loophole the post
  identifies — the buffer count is a genuine, exploitable signal.

So the simple claim "a CSM can never be counted" is **false for a partial-reservoir
machine**: a perfect counter who Wongs has a small but real edge. The catch is
volume — you play only ~1–8% of hands, so the hourly earn rate is a tiny sliver,
and it evaporates with any imperfection in tracking a 16-card window in real time.
(The idealised full-reshuffle CSM in [csm.md](csm.md) returns every card instantly
and stays exactly unbeatable.)

## Reproduce

```bash
# Per-count EV table + bet-spread edges
python csm_counting.py --rounds 200000000 --buffer 16 --out results/csm_counting.json

# A single window-counter run
python run_sim.py single --strategy window_counter --decks 6 --s17 --das --ls \
    --csm-buffer 16 --rounds 50000000 --cores 0
```

> The native (Cython) engine does not implement the windowed shoe, so these runs
> use the pure-Python engine (`engine="auto"` falls back automatically).
