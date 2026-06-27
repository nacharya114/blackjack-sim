"""The shoe: a shuffled multi-deck stack with a cut card and a running count.

Two dealing modes:

* **Sequential** (the default, and a `csm=True` full-reshuffle CSM): cards are
  dealt off the end of a shuffled list; reshuffle happens between rounds.
* **Windowed CSM** (`csm_buffer > 0`): models a *partial-reservoir* continuous
  shuffler. The last `csm_buffer` dealt cards are held out of the dealable pool,
  so every card is drawn uniformly from `full shoe - last csm_buffer cards`.
  That makes the Hi-Lo count of the cards in the buffer a live signal for the
  composition of the pool (see `docs/csm_counting.md`).
"""

from __future__ import annotations

import random

from .cards import HILO


class Shoe:
    """A dealing shoe of `decks` 52-card decks."""

    __slots__ = ("decks", "cut_card", "_cards", "_pos", "running_count", "_rng",
                 "csm_buffer", "_pool", "_pool_n", "_win", "_win_head", "_win_fill",
                 "_win_count")

    def __init__(self, decks: int, penetration: float, rng: random.Random | None = None,
                 csm_buffer: int = 0):
        self.decks = decks
        self._rng = rng or random.Random()
        total = decks * 52
        # Cut card position: cards remaining at which we should reshuffle next round.
        self.cut_card = int(total * (1.0 - penetration))
        self.csm_buffer = int(csm_buffer)
        self._cards: list[int] = []
        self._pos = 0
        self.running_count = 0
        # --- windowed-CSM state (only used when csm_buffer > 0) ---
        self._pool: list[int] = []
        self._pool_n = 0
        self._win: list[int] = []
        self._win_head = 0
        self._win_fill = 0
        self._win_count = 0
        self.shuffle()

    # ------------------------------------------------------------------ shuffle
    def shuffle(self) -> None:
        if self.csm_buffer > 0:
            # Full pool, empty buffer. The pool is a flat array we remove from /
            # append to in O(1); the buffer is a ring of the last N dealt cards.
            self._pool = [c for _ in range(self.decks) for c in range(52)]
            self._pool_n = len(self._pool)
            self._win = [0] * self.csm_buffer
            self._win_head = 0
            self._win_fill = 0
            self._win_count = 0
            self.running_count = 0
            return
        self._cards = [c for _ in range(self.decks) for c in range(52)]
        self._rng.shuffle(self._cards)
        self._pos = 0
        self.running_count = 0

    # -------------------------------------------------------------- properties
    @property
    def cards_remaining(self) -> int:
        if self.csm_buffer > 0:
            return self._pool_n          # always ~= full shoe - buffer
        return len(self._cards) - self._pos

    @property
    def decks_remaining(self) -> float:
        return self.cards_remaining / 52.0

    def needs_shuffle(self) -> bool:
        if self.csm_buffer > 0:
            return False                 # a CSM never stops to shuffle
        return self.cards_remaining <= self.cut_card

    def window_count(self) -> int:
        """Hi-Lo running count of the cards currently held in the buffer."""
        return self._win_count

    def true_count(self) -> float:
        if self.csm_buffer > 0:
            # The exploitable signal on a windowed CSM is the buffer's Hi-Lo
            # count (used directly, no divisor), not a shoe true count.
            return float(self._win_count)
        dr = self.decks_remaining
        return self.running_count / dr if dr > 0.25 else self.running_count * 4.0

    # ------------------------------------------------------------------- deal
    def _deal_windowed(self) -> int:
        """Draw a uniform card from the pool, push it into the buffer, and return
        the card that falls out of the buffer to the pool."""
        # remove a uniform-random card from the pool (swap-with-last)
        j = self._rng.randrange(self._pool_n)
        card = self._pool[j]
        self._pool_n -= 1
        self._pool[j] = self._pool[self._pool_n]
        self._pool[self._pool_n] = card
        # push into the ring buffer; evict the oldest once full
        if self._win_fill < self.csm_buffer:
            self._win[self._win_head] = card
            self._win_count += HILO[card]
            self._win_head = (self._win_head + 1) % self.csm_buffer
            self._win_fill += 1
        else:
            old = self._win[self._win_head]
            self._win_count -= HILO[old]
            self._pool[self._pool_n] = old           # return evicted card to pool
            self._pool_n += 1
            self._win[self._win_head] = card
            self._win_count += HILO[card]
            self._win_head = (self._win_head + 1) % self.csm_buffer
        return card

    def deal(self) -> int:
        """Deal one visible card and update the count."""
        if self.csm_buffer > 0:
            return self._deal_windowed()
        if self._pos >= len(self._cards):
            self._emergency_reshuffle()
        c = self._cards[self._pos]
        self._pos += 1
        self.running_count += HILO[c]
        return c

    def deal_hidden(self) -> int:
        """Deal the dealer hole card.

        Sequential mode: do NOT update the visible running count (the hole card
        is hidden until revealed). Windowed mode: the card physically leaves the
        machine into the buffer, but it is not read for a bet until the next
        round, by which point it has been revealed -- so buffer accounting is the
        same as any other deal.
        """
        if self.csm_buffer > 0:
            return self._deal_windowed()
        if self._pos >= len(self._cards):
            self._emergency_reshuffle()
        c = self._cards[self._pos]
        self._pos += 1
        return c

    def _emergency_reshuffle(self) -> None:
        """Fail-safe for a single deep-penetration round that out-draws the shoe."""
        self._cards = [c for _ in range(self.decks) for c in range(52)]
        self._rng.shuffle(self._cards)
        self._pos = 0
        self.running_count = 0

    def reveal(self, card: int) -> None:
        """Account for a previously hidden card (the hole card) in the count."""
        if self.csm_buffer > 0:
            return                       # already accounted for in the buffer
        self.running_count += HILO[card]
