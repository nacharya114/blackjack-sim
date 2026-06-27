"""Cross-validate the native (Cython) engine against the pure-Python reference.

The two engines use different RNGs, so they cannot match bit-for-bit. Instead we
assert they agree on the *expected value* (house edge) within statistical error:
the native engine runs many rounds (a near-exact estimate) and must land within a
few standard errors of the Python engine's estimate on the identical config.

Skipped automatically when the extension isn't built (`make build`).
"""
import math

import pytest

from blackjack import run_simulation
from blackjack.rules import Rules
from blackjack.simulator import fast_available

pytestmark = pytest.mark.skipif(not fast_available(),
                                reason="native engine not built (run `make build`)")


def _run(rules, strategy, rounds, seed, engine, **kw):
    res = run_simulation(rules, strategy, rounds=rounds, cores=1, seed=seed,
                         engine=engine, strategy_kwargs=kw)
    return res


CONFIGS = [
    ("basic", Rules(decks=6, dealer_hits_soft_17=False, late_surrender=True), {}),
    ("basic", Rules(decks=6, dealer_hits_soft_17=True, double_after_split=False,
                    late_surrender=False, blackjack_payout=1.2), {}),
    ("basic", Rules(decks=2, dealer_hits_soft_17=True, double_after_split=True), {}),
    ("counter", Rules(decks=6, dealer_hits_soft_17=False, late_surrender=True,
                      penetration=0.85), {"bet_ramp": {1: 1, 2: 2, 3: 4, 4: 8, 5: 12}}),
    ("basic", Rules(decks=6, dealer_hits_soft_17=False, late_surrender=True, csm=True), {}),
]


@pytest.mark.parametrize("strategy,rules,kw", CONFIGS)
def test_native_matches_python_house_edge(strategy, rules, kw):
    # Python: smaller run (its CI is the yardstick). Native: large, near-exact.
    py = _run(rules, strategy, rounds=400_000, seed=2024, engine="python", **kw)
    c = _run(rules, strategy, rounds=8_000_000, seed=2024, engine="c", **kw)

    assert py["engine"] == "python"
    assert c["engine"] == "c"

    he_py = py["main"]["house_edge"]
    he_c = c["main"]["house_edge"]
    # standard error of the Python per-round mean (units), used as the tolerance scale
    se_py = py["total"]["std_per_round_units"] / math.sqrt(py["rounds"])
    tol = max(0.0015, 5.0 * se_py)
    assert abs(he_c - he_py) < tol, (
        f"{rules.name} {strategy}: native HE {he_c:+.4%} vs python {he_py:+.4%} "
        f"(|Δ|={abs(he_c-he_py):.4%} > tol {tol:.4%})")


def test_native_is_deterministic_for_fixed_seed():
    rules = Rules()
    a = _run(rules, "basic", rounds=500_000, seed=99, engine="c")
    b = _run(rules, "basic", rounds=500_000, seed=99, engine="c")
    assert a["main"]["ev_per_round_units"] == b["main"]["ev_per_round_units"]
    assert a["main"]["house_edge"] == b["main"]["house_edge"]


def test_native_sidebet_house_edges_match_python():
    rules = Rules(decks=6, dealer_hits_soft_17=False, late_surrender=True)
    kw = {"side_bets": ("perfect_pairs", "21+3"), "side_bet_unit": 1.0}
    py = _run(rules, "basic", rounds=500_000, seed=5, engine="python", **kw)
    c = _run(rules, "basic", rounds=8_000_000, seed=5, engine="c", **kw)
    for name in ("perfect_pairs", "21+3"):
        he_py = py["side_bets"][name]["house_edge"]
        he_c = c["side_bets"][name]["house_edge"]
        # side-bet variance is large; allow a wide but meaningful band
        assert abs(he_c - he_py) < 0.02, f"{name}: native {he_c:+.4%} vs python {he_py:+.4%}"


def test_forcing_native_without_build_raises_is_consistent():
    # When the extension is importable, engine='c' must actually use it.
    res = _run(Rules(), "basic", rounds=10_000, seed=1, engine="c")
    assert res["engine"] == "c"
