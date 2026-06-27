"""CI guard: the hardcoded strategy tables must stay EV-correct.

Cross-checks `BASIC_HARD` and `ILLUSTRIOUS_18` against the analytic EV calculator
via `validate_strategy.py`. If someone edits a chart cell or an index into
something the EV math disagrees with (as happened with the 13v2/12v4 no-ops),
these tests fail.
"""
import validate_strategy as vs


def test_basic_hard_chart_is_ev_optimal_s17_and_h17():
    for h17 in (False, True):
        rows, mismatches, _ties = vs.validate_basic(h17=h17, tol=0.005)
        assert len(rows) == 170
        assert mismatches == [], (
            f"{'H17' if h17 else 'S17'} chart disagrees with EV-optimal play: "
            + ", ".join(f"{m['total']}v{m['up']} chart={m['chart']} "
                        f"opt={m['ev_optimal']} gap={m['ev_gap']}" for m in mismatches))


def test_illustrious18_indices_confirmed_by_derived_crossovers():
    derived = vs.derive_indices(-7.0, 10.0, 0.5)
    confirmed, _extra = vs.match_indices(derived)
    assert len(confirmed) == len(vs.ILLUSTRIOUS_18)
    bad = [c for c in confirmed if not c["ok"]]
    assert bad == [], (
        "indices whose EV crossover is >1.5 off: "
        + ", ".join(f"{c['hand']} idx={c['index']} derived={c['derived_cross']}"
                    for c in bad))
