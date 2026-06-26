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
