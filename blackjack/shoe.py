"""The shoe: a shuffled multi-deck stack with a cut card and a running count."""

from __future__ import annotations

import random

from .cards import HILO


class Shoe:
    """A dealing shoe of `decks` 52-card decks.

    Cards are dealt off the end of a Python list (fast pop). When the number of
    cards dealt crosses the penetration threshold a reshuffle is flagged; the
    game reshuffles between rounds, never mid-round.
    """

    __slots__ = ("decks", "cut_card", "_cards", "_pos", "running_count", "_rng")

    def __init__(self, decks: int, penetration: float, rng: random.Random | None = None):
        self.decks = decks
        self._rng = rng or random.Random()
        total = decks * 52
        # Cut card position: cards remaining at which we should reshuffle next round.
        self.cut_card = int(total * (1.0 - penetration))
        self._cards: list[int] = []
        self._pos = 0
        self.running_count = 0
        self.shuffle()

    def shuffle(self) -> None:
        self._cards = [c for _ in range(self.decks) for c in range(52)]
        self._rng.shuffle(self._cards)
        self._pos = 0
        self.running_count = 0

    @property
    def cards_remaining(self) -> int:
        return len(self._cards) - self._pos

    @property
    def decks_remaining(self) -> float:
        return self.cards_remaining / 52.0

    def needs_shuffle(self) -> bool:
        return self.cards_remaining <= self.cut_card

    def true_count(self) -> float:
        dr = self.decks_remaining
        return self.running_count / dr if dr > 0.25 else self.running_count * 4.0

    def deal(self) -> int:
        """Deal one visible card and update the running count."""
        if self._pos >= len(self._cards):
            self._emergency_reshuffle()
        c = self._cards[self._pos]
        self._pos += 1
        self.running_count += HILO[c]
        return c

    def deal_hidden(self) -> int:
        """Deal the dealer hole card WITHOUT updating the visible running count."""
        if self._pos >= len(self._cards):
            self._emergency_reshuffle()
        c = self._cards[self._pos]
        self._pos += 1
        return c

    def _emergency_reshuffle(self) -> None:
        """Fail-safe for a single deep-penetration round that out-draws the shoe.

        Vanishingly rare except on a 1-deck shoe with very deep penetration;
        statistically negligible but keeps the simulation from crashing.
        """
        self._cards = [c for _ in range(self.decks) for c in range(52)]
        self._rng.shuffle(self._cards)
        self._pos = 0
        self.running_count = 0

    def reveal(self, card: int) -> None:
        """Account for a previously hidden card (the hole card) in the count."""
        self.running_count += HILO[card]
