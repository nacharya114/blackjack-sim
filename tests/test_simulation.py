"""Integration tests for the Monte-Carlo driver.

These run small, fixed-seed, single-core simulations so they are fully
deterministic and fast. Assertions use loose magnitude bands plus directional
relationships (which hold far inside any Monte-Carlo noise) rather than exact
numbers, so they validate the engine without being brittle.
"""
import pytest

from blackjack import Rules, run_simulation

ROUNDS = 200_000
SEED = 12345


def _he(rules, strategy="basic", **kw):
    res = run_simulation(rules, strategy, rounds=ROUNDS, cores=1, seed=SEED,
                         strategy_kwargs=kw or None)
    return res


def test_basic_strategy_house_edge_in_published_band():
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=True, blackjack_payout=1.5, penetration=0.75)
    res = _he(r)
    # Published basic-strategy edge for this game is ~0.4%. Allow a wide band
    # for 200k-round Monte-Carlo noise but pin the order of magnitude.
    assert 0.0 < res["main"]["house_edge"] < 0.009
    # Structural invariants.
    assert res["rounds"] == ROUNDS
    assert res["main"]["avg_initial_units"] == pytest.approx(1.0, abs=1e-9)
    assert res["total"]["std_per_round_units"] > 0


def test_6to5_is_worse_than_3to2():
    base = dict(decks=6, dealer_hits_soft_17=True, double_after_split=False,
                late_surrender=False, penetration=0.75)
    he_3to2 = _he(Rules(blackjack_payout=1.5, **base))["main"]["house_edge"]
    he_6to5 = _he(Rules(blackjack_payout=1.2, **base))["main"]["house_edge"]
    # 6:5 should cost the player well over a full percent versus 3:2.
    assert he_6to5 - he_3to2 > 0.01


def test_counting_beats_flat_basic_on_deep_shoe():
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=True, blackjack_payout=1.5, penetration=0.85)
    basic = _he(r)["main"]["house_edge"]
    counter = _he(r, strategy="counter")["main"]["house_edge"]
    # A ramped Hi-Lo counter should improve the edge (lower house edge).
    assert counter < basic


def test_csm_basic_house_edge_in_published_band():
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=True, blackjack_payout=1.5, csm=True)
    res = _he(r)
    assert res["rules_name"] == "6D CSM S17 DAS LS 3:2"
    # A CSM deals a fresh shoe every round -> the fresh-shoe basic edge (~0.3%).
    assert 0.0 < res["main"]["house_edge"] < 0.009


def test_csm_kills_card_counting():
    csm = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                late_surrender=True, blackjack_payout=1.5, csm=True)
    shoe = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                 late_surrender=True, blackjack_payout=1.5, penetration=0.85)
    counter_csm = _he(csm, strategy="counter")["main"]["house_edge"]
    counter_shoe = _he(shoe, strategy="counter")["main"]["house_edge"]
    basic_csm = _he(csm)["main"]["house_edge"]
    # On a deep shoe counting wins; on a CSM the same counter is stuck at the
    # house edge -- a large, unambiguous gap.
    assert counter_csm - counter_shoe > 0.008
    # And a counter on a CSM is no better than flat basic strategy.
    assert abs(counter_csm - basic_csm) < 0.003


def test_csm_counter_cannot_ramp_its_bets():
    csm = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                late_surrender=True, blackjack_payout=1.5, csm=True)
    shoe = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                 late_surrender=True, blackjack_payout=1.5, penetration=0.85)
    std_csm = _he(csm, strategy="counter")["total"]["std_per_round_units"]
    std_shoe = _he(shoe, strategy="counter")["total"]["std_per_round_units"]
    # The true count never builds on a CSM, so the ramp stays flat: per-round
    # volatility is far below a real spread on a deep shoe.
    assert std_csm < 1.6
    assert std_shoe > std_csm + 1.0


def test_side_bet_breakdown_present():
    r = Rules(decks=6, penetration=0.75)
    res = _he(r, side_bets=("perfect_pairs",), side_bet_unit=1.0)
    assert "perfect_pairs" in res["side_bets"]
    # Perfect Pairs carries a hefty house edge (~6%).
    assert res["side_bets"]["perfect_pairs"]["house_edge"] > 0.03


def test_custom_bet_ramp_widens_variance():
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=False, blackjack_payout=1.5, penetration=0.75)
    narrow = _he(r, strategy="counter", bet_ramp={1: 1, 2: 1, 3: 2, 4: 2, 5: 3})
    wide = _he(r, strategy="counter", bet_ramp={1: 1, 2: 3, 3: 6, 4: 9, 5: 12})
    # A wider spread must raise per-round volatility.
    assert (wide["total"]["std_per_round_units"]
            > narrow["total"]["std_per_round_units"])
