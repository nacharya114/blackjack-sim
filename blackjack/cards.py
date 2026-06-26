"""Card encoding and fast lookup tables.

A card is a single integer 0..51.
    rank_index = card % 13   # 0->'2', 1->'3', ... 7->'9', 8->'10', 9->'J', 10->'Q', 11->'K', 12->'A'
    suit       = card // 13  # 0..3  (clubs, diamonds, hearts, spades)

All per-card properties are precomputed into module-level tuples so the hot
simulation loop only does list indexing, never branching on rank.
"""

from __future__ import annotations

RANK_LABELS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A")
SUIT_LABELS = ("c", "d", "h", "s")

# Blackjack point value, counting every Ace as 11 (soft). Tens/faces -> 10.
_BJ_BY_RANK = (2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11)
# "High card" rank used for poker-style side bets (straights/flushes): A high = 14.
_HIGHRANK_BY_RANK = (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
# Hi-Lo counting tag.
_HILO_BY_RANK = (1, 1, 1, 1, 1, 0, 0, 0, -1, -1, -1, -1, -1)

BJ_VALUE = tuple(_BJ_BY_RANK[c % 13] for c in range(52))
HIGH_RANK = tuple(_HIGHRANK_BY_RANK[c % 13] for c in range(52))
HILO = tuple(_HILO_BY_RANK[c % 13] for c in range(52))
IS_ACE = tuple(1 if (c % 13) == 12 else 0 for c in range(52))
IS_TEN = tuple(1 if BJ_VALUE[c] == 10 else 0 for c in range(52))
RANK_INDEX = tuple(c % 13 for c in range(52))   # 0..12, useful for pair detection
SUIT = tuple(c // 13 for c in range(52))


def card_name(card: int) -> str:
    return f"{RANK_LABELS[card % 13]}{SUIT_LABELS[card // 13]}"


def hand_total(cards) -> tuple[int, bool]:
    """Return (best_total, is_soft) for a list of card ints.

    is_soft is True when an Ace is still being counted as 11 in the best total.
    """
    total = 0
    aces = 0
    for c in cards:
        total += BJ_VALUE[c]
        aces += IS_ACE[c]
    # Demote aces from 11 to 1 while busting.
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def is_blackjack(cards) -> bool:
    """Natural blackjack: exactly two cards totalling 21."""
    return len(cards) == 2 and hand_total(cards)[0] == 21
