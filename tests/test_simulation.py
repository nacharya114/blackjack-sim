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


def test_windowed_csm_buffer_signal_frequency():
    """A windowed CSM's buffer count is a live signal; per discountgambling.net it
    is >= +5 about 8% of the time."""
    import random

    from blackjack.shoe import Shoe
    rng = random.Random(1)
    shoe = Shoe(6, 0.0, rng=rng, csm_buffer=16)
    samples = []
    for i in range(200_000):
        if i >= 1000:                       # let the 16-card buffer fill
            samples.append(shoe.window_count())
        for _ in range(5):                  # roughly a heads-up round of cards
            shoe.deal()
    assert all(-16 <= c <= 16 for c in samples)
    ge5 = sum(1 for c in samples if c >= 5) / len(samples)
    assert 0.05 < ge5 < 0.12                # ~8% (loose band for the deal pattern)


def test_windowed_csm_counter_ramps_on_buffer_signal():
    """Unlike a full-reshuffle CSM (count dead, flat bets), a *windowed* CSM gives
    the window counter a real signal, so its bet ramp engages and variance rises."""
    rules = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                  late_surrender=True, csm_buffer=16)
    flat_sd = _he(rules)["total"]["std_per_round_units"]
    ramp = {5: 8, 6: 12, 7: 16, 8: 20, 9: 20}
    spread = _he(rules, strategy="window_counter", bet_ramp=ramp)
    # The bet ramp engages on the buffer signal, so variance climbs well above a
    # flat better (on a *full-reshuffle* CSM the signal is dead and it would not).
    # (Whether this beats the house needs far more rounds -- see docs/csm_counting.md.)
    assert spread["total"]["std_per_round_units"] > flat_sd + 0.5


def test_side_bet_breakdown_present():
    r = Rules(decks=6, penetration=0.75)
    res = _he(r, side_bets=("perfect_pairs",), side_bet_unit=1.0)
    assert "perfect_pairs" in res["side_bets"]
    # Perfect Pairs carries a hefty house edge (~6%).
    assert res["side_bets"]["perfect_pairs"]["house_edge"] > 0.03


def test_wonging_out_negative_counts_beats_playing_through():
    """Sitting out deep-negative counts (bet 0 below TC -1) instead of grinding
    the table minimum through them raises the counter's edge: the same positive
    ramp keeps all its winning action but sheds the negative-EV low-count rounds.
    Same seed (common random numbers) so the bet policy, not RNG, drives the gap.
    """
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=True, blackjack_payout=1.5, penetration=0.85)
    positive = {1: 1, 2: 2, 3: 4, 4: 8, 5: 12}
    play_through = _he(r, strategy="counter", bet_ramp=positive)  # min_bet defaults to 1
    wong = run_simulation(r, "counter", rounds=ROUNDS, cores=1, seed=SEED,
                          strategy_kwargs={"bet_ramp": {-1: 1, 0: 1, **positive},
                                           "min_bet": 0.0})
    # Wonging improves EV/round and pushes the house edge further in the player's
    # favour (more negative). Margins sit well inside common-random-number noise.
    assert (wong["total"]["ev_per_round_units"]
            > play_through["total"]["ev_per_round_units"] + 0.001)
    assert wong["main"]["house_edge"] < play_through["main"]["house_edge"] - 0.001


def test_custom_bet_ramp_widens_variance():
    r = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
              late_surrender=False, blackjack_payout=1.5, penetration=0.75)
    narrow = _he(r, strategy="counter", bet_ramp={1: 1, 2: 1, 3: 2, 4: 2, 5: 3})
    wide = _he(r, strategy="counter", bet_ramp={1: 1, 2: 3, 3: 6, 4: 9, 5: 12})
    # A wider spread must raise per-round volatility.
    assert (wide["total"]["std_per_round_units"]
            > narrow["total"]["std_per_round_units"])
