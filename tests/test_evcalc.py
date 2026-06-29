"""Tests for the analytic EV calculator behind the strategy panel.

Uses robust directional checks (EV orderings flip across the index) rather than
exact numbers, plus the engine-index cross-check that powers the panel.
"""
import pytest

from blackjack.evcalc import EVModel, densities
from blackjack.strategy import ILLUSTRIOUS_18, BASIC_HARD


def test_densities_normalise_and_track_count():
    for tc in (-6, 0, 5):
        p = densities(tc)
        assert sum(p.values()) == pytest.approx(1.0, abs=1e-12)
        # Expected Hi-Lo tag of the next card is exactly -tc/52.
        tag = sum(p[v] * (1 if 2 <= v <= 6 else -1 if v in (10, 11) else 0)
                  for v in p)
        assert tag == pytest.approx(-tc / 52, abs=1e-12)


def test_dealer_distribution_is_a_distribution():
    m = EVModel(0.0)
    for up in (2, 6, 10, 11):
        dist = m.dealer_dist_from_up(up)
        assert sum(dist.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(p >= 0 for p in dist.values())


def test_insurance_turns_profitable_with_count():
    assert EVModel(0.0).insurance_ev() < 0          # bad bet at neutral count
    assert EVModel(5.0).insurance_ev() > 0          # +EV once tens are dense
    # crossover sits near the classic +3 index
    assert EVModel(3.0).insurance_ev() == pytest.approx(0.0, abs=0.02)


def test_action_ordering_flips_across_index():
    # 16 vs 10 (index 0): hit better when negative, stand better when positive.
    lo = EVModel(-3.0).action_evs(16, 10)
    hi = EVModel(+3.0).action_evs(16, 10)
    assert lo["hit"] > lo["stand"]
    assert hi["stand"] > hi["hit"]

    # 11 vs A (index +1, S17): hit better below, double better above.
    lo = EVModel(-2.0).action_evs(11, 11)
    hi = EVModel(+3.0).action_evs(11, 11)
    assert lo["hit"] > lo["double"]
    assert hi["double"] > hi["hit"]


def test_split_ev_appears_only_for_pairs():
    m = EVModel(0.0)
    plain = m.action_evs(16, 10)
    assert "split" not in plain
    paired = m.action_evs(16, 10, pair=8)
    assert "split" in paired
    # the standalone helper agrees with the action_evs convenience key
    assert paired["split"] == pytest.approx(m.split_ev(8, 10), abs=1e-12)


def test_always_split_eights_and_aces():
    """8,8 and A,A are the textbook 'always split' pairs — split must dominate
    hitting/standing the underlying total against every upcard (no surrender)."""
    m = EVModel(0.0)
    for up in (2, 6, 9, 10, 11):
        e8 = m.action_evs(16, up, pair=8)
        assert e8["split"] > max(e8["stand"], e8["hit"]), f"8,8 v{up}"
        ea = m.action_evs(12, up, soft=True, pair=11)
        assert ea["split"] > max(ea["stand"], ea["hit"], ea["double"]), f"A,A v{up}"


def test_never_split_tens_or_fives():
    m = EVModel(0.0)
    for up in (5, 6, 9):
        et = m.action_evs(20, up, pair=10)
        assert et["stand"] > et["split"]                 # stand on 20, never split
        ef = m.action_evs(10, up, pair=5)
        assert ef["double"] > ef["split"]                # double the 10, never split


def test_das_and_resplit_aces_raise_split_value():
    m = EVModel(0.0)
    # 2,2 v 4 splits only with DAS in basic strategy; DAS must not lower the split EV.
    assert m.split_ev(2, 4, das=True) > m.split_ev(2, 4, das=False)
    # Allowing resplit of aces can only add value.
    assert m.split_ev(11, 6, resplit_aces=True) >= m.split_ev(11, 6, resplit_aces=False)
    # Split aces take one card unless hit_split_aces; allowing hits/doubles helps.
    assert m.split_ev(11, 6, hit_split_aces=True) >= m.split_ev(11, 6, hit_split_aces=False)


def _col(up):
    return 9 if up == 11 else up - 2


def _crossover(direction, basic_evs, dev_evs, grid):
    diff = [d - b for d, b in zip(dev_evs, basic_evs)]
    for i in range(1, len(grid)):
        a, b = diff[i - 1], diff[i]
        if (a < 0) != (b < 0):
            return grid[i - 1] + (grid[i] - grid[i - 1]) * (-a) / (b - a)
    return None


def test_ev_crossovers_match_engine_indices():
    """Every Illustrious-18 EV crossover should land within ~1.5 of the engine's
    published index — the property the strategy panel visualises."""
    ACT = {"S": "stand", "H": "hit", "D": "double", "R": "surrender"}
    grid = [round(-7 + 0.5 * i, 2) for i in range(31)]   # -7 .. +8
    models = [EVModel(tc) for tc in grid]
    for total, soft, up, dev_code, thr, _direction in ILLUSTRIOUS_18:
        basic_code = BASIC_HARD[total][_col(up)]
        b_evs = [m.action_evs(total, up)[ACT[basic_code]] for m in models]
        d_evs = [m.action_evs(total, up)[ACT[dev_code]] for m in models]
        xo = _crossover(_direction, b_evs, d_evs, grid)
        assert xo is not None, f"{total}v{up}: no crossover found"
        assert abs(xo - thr) <= 1.5, f"{total}v{up}: crossover {xo:.2f} vs index {thr}"
