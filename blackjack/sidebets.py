"""Side bets, resolved at the deal from the player's two cards (+ dealer upcard).

Net payout convention: a win that pays "X:1" returns +X (profit, stake kept);
a loss returns -1 (stake lost). EV per unit staked is the mean net payout.

Paytables are configurable; the defaults are common Las Vegas paytables.
"""

from __future__ import annotations

from .cards import RANK_INDEX, SUIT, HIGH_RANK

# colour: clubs(0)/spades(3) black, diamonds(1)/hearts(2) red
_COLOR = tuple(1 if SUIT[c] in (1, 2) else 0 for c in range(52))

DEFAULT_PERFECT_PAIRS = {"perfect": 25, "colored": 12, "mixed": 6}
DEFAULT_21P3 = {"suited_trips": 100, "straight_flush": 40, "trips": 30,
                "straight": 10, "flush": 5}

SIDE_BETS = ("perfect_pairs", "21+3")


def perfect_pairs_net(c0: int, c1: int, paytable: dict | None = None) -> float:
    pt = paytable or DEFAULT_PERFECT_PAIRS
    if RANK_INDEX[c0] != RANK_INDEX[c1]:
        return -1.0
    if SUIT[c0] == SUIT[c1]:
        return float(pt["perfect"])
    if _COLOR[c0] == _COLOR[c1]:
        return float(pt["colored"])
    return float(pt["mixed"])


def _is_straight(ranks: tuple[int, int, int]) -> bool:
    s = sorted(ranks)
    if s[0] + 1 == s[1] and s[1] + 1 == s[2]:
        return True
    # Ace-low wheel: A(14),2,3 -> treat ace as 1
    if set(s) == {2, 3, 14}:
        return True
    return False


def twentyone_plus_three_net(c0: int, c1: int, up: int,
                             paytable: dict | None = None) -> float:
    pt = paytable or DEFAULT_21P3
    cards = (c0, c1, up)
    ranks = tuple(RANK_INDEX[c] for c in cards)
    high = tuple(HIGH_RANK[c] for c in cards)
    suits = tuple(SUIT[c] for c in cards)

    same_rank = ranks[0] == ranks[1] == ranks[2]
    same_suit = suits[0] == suits[1] == suits[2]
    straight = _is_straight(high)

    if same_rank and same_suit:
        return float(pt["suited_trips"])
    if straight and same_suit:
        return float(pt["straight_flush"])
    if same_rank:
        return float(pt["trips"])
    if straight:
        return float(pt["straight"])
    if same_suit:
        return float(pt["flush"])
    return -1.0


def resolve_side_bets(player_cards, dealer_up, active: dict | None,
                      paytables: dict | None = None) -> dict:
    """Return {bet_name: net_payout} for each active side bet.

    `active` is a set/dict of side-bet names to evaluate; if None, none are played.
    """
    if not active:
        return {}
    paytables = paytables or {}
    out = {}
    c0, c1 = player_cards[0], player_cards[1]
    if "perfect_pairs" in active:
        out["perfect_pairs"] = perfect_pairs_net(c0, c1, paytables.get("perfect_pairs"))
    if "21+3" in active:
        out["21+3"] = twentyone_plus_three_net(c0, c1, dealer_up, paytables.get("21+3"))
    return out
