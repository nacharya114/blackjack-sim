"""Fast, deterministic unit tests for the engine primitives."""
import random

import pytest

from blackjack import Rules, Shoe
from blackjack.cards import (
    BJ_VALUE, HILO, IS_ACE, hand_total, is_blackjack, card_name,
)
from blackjack import sidebets as sb


# --- cards ------------------------------------------------------------------
def test_hand_total_hard_and_soft():
    # A + 6 = soft 17
    ace, six = 12, 4            # rank indices 12 (A) and 4 (6) in suit 0
    assert hand_total([ace, six]) == (17, True)
    # A + 6 + 10 -> ace demoted to 1 -> hard 17
    ten = 8
    assert hand_total([ace, six, ten]) == (17, False)
    # two aces = soft 12 (one ace counts as 11)
    assert hand_total([ace, ace + 0]) == (12, True)


def test_hand_total_demotes_aces_to_avoid_bust():
    ace, eight = 12, 6          # rank index 6 == '8'
    # A A A 8 -> 11 + 1 + 1 + 8 = 21, still soft (one ace stays an 11)
    assert hand_total([ace, ace, ace, eight]) == (21, True)
    # 10 10 A -> ace must drop to 1 -> hard 21
    ten = 8
    assert hand_total([ten, ten, ace]) == (21, False)


def test_is_blackjack():
    ace, king = 12, 11
    assert is_blackjack([ace, king]) is True
    assert is_blackjack([ace, king, 0]) is False          # 3 cards
    assert is_blackjack([8, 8]) is False                  # 10+10=20


def test_lookup_tables_consistent():
    assert len(BJ_VALUE) == 52 and len(HILO) == 52
    # A full deck is Hi-Lo balanced.
    assert sum(HILO[c] for c in range(52)) == 0
    # Aces are valued 11 and flagged.
    aces = [c for c in range(52) if IS_ACE[c]]
    assert len(aces) == 4
    assert all(BJ_VALUE[a] == 11 for a in aces)


def test_card_name():
    assert card_name(12) == "Ac"      # ace of clubs
    assert card_name(13 * 3 + 11) == "Ks"  # king of spades


# --- rules ------------------------------------------------------------------
def test_rules_name_and_roundtrip():
    r = Rules(decks=6, dealer_hits_soft_17=True, double_after_split=False,
              late_surrender=False, blackjack_payout=1.2)
    assert r.name == "6D H17 noDAS noLS 6:5"
    again = Rules.from_dict(r.to_dict())
    assert again.to_dict() == r.to_dict()
    assert isinstance(again.double_totals, tuple)


def test_can_double_total():
    r = Rules(double_allowed=True, double_totals=(9, 11))
    assert r.can_double_total(10) is True
    assert r.can_double_total(8) is False
    r2 = Rules(double_allowed=False)
    assert r2.can_double_total(11) is False


# --- shoe --------------------------------------------------------------------
def test_shoe_count_and_reshuffle():
    shoe = Shoe(decks=1, penetration=0.5, rng=random.Random(1))
    assert shoe.cut_card == 26
    assert shoe.cards_remaining == 52
    # Deal the whole deck; the running count must return to zero (balanced).
    for _ in range(52):
        shoe.deal()
    assert shoe.running_count == 0
    assert shoe.needs_shuffle() is True


def test_shoe_true_count_scales_with_decks_remaining():
    shoe = Shoe(decks=6, penetration=0.75, rng=random.Random(2))
    # Force a known running count and check true count = rc / decks_remaining.
    shoe.running_count = 6
    tc = shoe.true_count()
    assert tc == pytest.approx(6 / shoe.decks_remaining, rel=1e-9)


def test_shoe_is_deterministic_under_seed():
    a = Shoe(decks=2, penetration=0.8, rng=random.Random(42))
    b = Shoe(decks=2, penetration=0.8, rng=random.Random(42))
    assert [a.deal() for _ in range(40)] == [b.deal() for _ in range(40)]


# --- side bets ---------------------------------------------------------------
def _card(rank_index, suit):
    return suit * 13 + rank_index


def test_perfect_pairs_tiers():
    # perfect = same rank & suit
    assert sb.perfect_pairs_net(_card(5, 0), _card(5, 0)) == 25
    # colored = same rank, same colour, different suit (clubs & spades both black)
    assert sb.perfect_pairs_net(_card(5, 0), _card(5, 3)) == 12
    # mixed = same rank, different colour (clubs black, hearts red)
    assert sb.perfect_pairs_net(_card(5, 0), _card(5, 2)) == 6
    # no pair
    assert sb.perfect_pairs_net(_card(5, 0), _card(6, 0)) == -1.0


def test_21plus3_combinations():
    # suited trips: three 7s same suit
    seven = 5
    assert sb.twentyone_plus_three_net(_card(seven, 1), _card(seven, 1), _card(seven, 1)) == 100
    # straight flush: 5,6,7 same suit (rank indices 3,4,5)
    assert sb.twentyone_plus_three_net(_card(3, 2), _card(4, 2), _card(5, 2)) == 40
    # plain trips (mixed suits)
    assert sb.twentyone_plus_three_net(_card(seven, 0), _card(seven, 1), _card(seven, 2)) == 30
    # straight, mixed suits
    assert sb.twentyone_plus_three_net(_card(3, 0), _card(4, 1), _card(5, 2)) == 10
    # flush only
    assert sb.twentyone_plus_three_net(_card(2, 3), _card(7, 3), _card(10, 3)) == 5
    # ace-low wheel A-2-3 counts as a straight
    ace = 12
    assert sb.twentyone_plus_three_net(_card(ace, 0), _card(0, 1), _card(1, 2)) == 10
    # nothing
    assert sb.twentyone_plus_three_net(_card(0, 0), _card(5, 1), _card(10, 2)) == -1.0


def test_resolve_side_bets_only_active():
    pc = [_card(5, 0), _card(5, 0)]
    out = sb.resolve_side_bets(pc, _card(0, 0), {"perfect_pairs"})
    assert set(out) == {"perfect_pairs"}
    assert sb.resolve_side_bets(pc, _card(0, 0), None) == {}
