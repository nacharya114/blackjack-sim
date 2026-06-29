"""Tests for the dashboard builder and bet-spread helpers (no matplotlib needed)."""
import json

import pytest


import betspread
import optimize_betspread as opt
import optimizer_data as od
import strategy_chart as sc
import visualize
from blackjack.evcalc import EVModel
from blackjack.rules import Rules


# --- betspread ramp / breakeven math ----------------------------------------
def test_build_ramp_endpoints_and_monotonic():
    flat = betspread.build_ramp(1)
    assert set(flat.values()) == {1}                      # 1-1 spread is flat
    ramp = betspread.build_ramp(8)
    assert ramp[1] == 1 and ramp[betspread.TOP_TC] == 8
    vals = [ramp[k] for k in sorted(ramp)]
    assert vals == sorted(vals)                           # non-decreasing


def test_interpolate_breakeven_brackets_zero_crossing():
    rows = [
        {"top": 2, "total_house_edge": 0.0020},
        {"top": 3, "total_house_edge": 0.0008},
        {"top": 4, "total_house_edge": -0.0006},          # crosses here
        {"top": 5, "total_house_edge": -0.0020},
    ]
    be = betspread.interpolate_breakeven(rows)
    assert 3 < be < 4


def test_interpolate_breakeven_none_when_never_crosses():
    rows = [{"top": 1, "total_house_edge": 0.01},
            {"top": 2, "total_house_edge": 0.008}]
    assert betspread.interpolate_breakeven(rows) is None


# --- optimizer ramp construction --------------------------------------------
def test_opt_build_ramp_endpoints_clamped_and_monotonic():
    ramp = opt.build_ramp(cap=20, ramp_start=2, top_tc=5, gamma=2.0, wong=-1)
    # Cover bets at the table minimum from the Wong threshold up to the ramp.
    assert ramp[-1] == 1 and ramp[0] == 1 and ramp[1] == 1
    # Ramp tops out exactly at the cap at the top true count.
    assert ramp[5] == 20
    # No key below the Wong threshold (those counts are sat out via min_bet=0).
    assert min(ramp) == -1
    vals = [ramp[k] for k in sorted(ramp)]
    assert vals == sorted(vals)                       # non-decreasing
    assert all(1 <= v <= 20 for v in vals)            # clamped to [1, cap]


def test_opt_gamma_backloads_the_ramp():
    """Higher gamma holds smaller bets through marginal counts (more back-loaded);
    lower gamma jumps toward the cap earlier. Compare a mid-ramp count."""
    front = opt.build_ramp(cap=40, ramp_start=1, top_tc=5, gamma=0.5, wong=-1)
    back = opt.build_ramp(cap=40, ramp_start=1, top_tc=5, gamma=3.0, wong=-1)
    mid = 3                                            # a count strictly inside the ramp
    assert front[mid] > back[mid]
    # Both still pin the same endpoints.
    assert front[5] == back[5] == 40
    assert front[1] == back[1] == 1


def test_opt_wong_threshold_controls_lowest_key():
    deep = opt.build_ramp(cap=12, ramp_start=2, top_tc=5, gamma=1.0, wong=-2)
    assert min(deep) == -2 and deep[-2] == 1
    # Play-through style (wong at the ramp start) keeps only ramping keys.
    pt = opt.build_ramp(cap=12, ramp_start=1, top_tc=5, gamma=1.0, wong=1)
    assert min(pt) == 1


def test_opt_ramp_str_is_sorted_and_compact():
    s = opt.ramp_str({2: 3, -1: 1, 0: 1})
    assert s == "-1:1,0:1,2:3"


# --- optimizer-tab closed-form model ----------------------------------------
def test_closed_form_metrics_matches_hand_computation():
    # Two true-count buckets; verify EV, variance and house edge by hand.
    buckets = [
        {"tc": 0, "p": 0.8, "m": -0.01, "v": 1.30},   # bet 1u
        {"tc": 3, "p": 0.2, "m": 0.05, "v": 1.20},    # bet 4u
    ]
    bet_of = od.ramp_bet_of({0: 1, 3: 4}, 1.0)
    r = od.closed_form_metrics(buckets, bet_of)
    ev = 0.8 * 1 * -0.01 + 0.2 * 4 * 0.05            # = 0.032
    e2 = 0.8 * 1 * (1.30 + 0.01**2) + 0.2 * 16 * (1.20 + 0.05**2)
    var = e2 - ev * ev
    assert r["ev_per_round_units"] == pytest.approx(ev)
    assert r["std_per_round_units"] == pytest.approx(var ** 0.5)
    assert r["score"] == pytest.approx((ev / var ** 0.5) ** 2 * 1e6)
    assert r["avg_initial_units"] == pytest.approx(0.8 * 1 + 0.2 * 4)   # 1.6
    assert r["house_edge"] == pytest.approx(-ev / 1.6)


def test_ramp_bet_of_sitout_cover_and_cap():
    bet_of = od.ramp_bet_of({-1: 1, 0: 1, 1: 1, 2: 4, 3: 8}, 0.0)
    assert bet_of(-2) == 0.0          # below the lowest key -> sit out (min_bet 0)
    assert bet_of(-1) == 1.0          # cover at the table minimum
    assert bet_of(2) == 4.0
    assert bet_of(9) == 8.0           # at/above the top key -> top bet


def test_closed_form_equals_simulation_same_seed():
    """The closed-form weighted sum over buckets is an identity: with the same
    seed (identical card sequences, only bets differ) it reproduces the engine's
    ramp EV exactly. This is the model the dashboard optimizer tab runs on."""
    from blackjack import Rules, run_simulation
    rules = Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                  late_surrender=True, blackjack_payout=1.5, penetration=0.75)
    ramp = {1: 1, 2: 3, 3: 6, 4: 9}
    buckets = od.measure_buckets(rules, 120_000, cores=1, seed=321)
    cf = od.closed_form_metrics(buckets, od.ramp_bet_of(ramp, 1.0))
    mc = run_simulation(rules, "counter", rounds=120_000, cores=1, seed=321,
                        strategy_kwargs={"bet_ramp": dict(ramp), "min_bet": 1.0},
                        engine="python")
    assert cf["ev_per_round_units"] == pytest.approx(
        mc["total"]["ev_per_round_units"], abs=1e-12)


# --- optimizer panel inlining + betspread schema filtering ------------------
def test_discover_betspread_skips_optimizer_files(tmp_path):
    # A betspread.py breakeven file (kept) and an optimize_betspread.py file (dropped).
    (tmp_path / "betspread_pen75.json").write_text(json.dumps({
        "penetration": 0.75, "results": [{"top": 1, "total_house_edge": 0.003}]}))
    (tmp_path / "betspread_opt_pen75.json").write_text(json.dumps({
        "penetration": 0.75, "caps": [{"cap": 12}], "references": []}))
    found = visualize.discover_betspread(str(tmp_path), None)
    assert len(found) == 1 and "caps" not in found[0]


def test_build_dashboard_inlines_optimizer(tmp_path):
    out = tmp_path / "dashboard.html"
    optimizer = {"generated": "2026-01-01 00:00", "tc_min": -2, "tc_max": 4,
                 "configs": [{"id": "X", "label": "6D S17", "penetration": 0.75,
                              "tc_min": -2, "tc_max": 4,
                              "buckets": [{"tc": 0, "p": 0.7, "m": -0.01, "v": 1.3},
                                          {"tc": 3, "p": 0.3, "m": 0.05, "v": 1.2}],
                              "validation": {"ramp": {"3": 4}}}]}
    visualize.build_dashboard(_fake_sweep(), visualize.TEMPLATE, str(out),
                              optimizer=optimizer)
    html = out.read_text()
    assert "/*__OPTIMIZER_DATA__*/" not in html      # marker fully replaced
    import re
    m = re.search(r"const OPTIMIZER_DATA = (\{.*?\});", html)
    assert json.loads(m.group(1))["configs"][0]["label"] == "6D S17"


# --- dashboard build (inlining) ---------------------------------------------
def _fake_sweep():
    return {"generated": "2026-01-01 00:00", "rounds_each": 1000,
            "dollars_per_unit": 10.0, "results": [{
                "label": "X", "rules_name": "6D S17 DAS LS 3:2", "strategy": "basic",
                "rounds": 1000, "main": {"house_edge": 0.004, "element_of_risk": 0.0035,
                "ev_per_round_units": -0.004, "std_per_round_units": 1.1,
                "avg_wager_units": 1.1, "avg_initial_units": 1.0},
                "side_bets": {}, "total": {"ev_per_round_units": -0.004,
                "house_edge": 0.0035, "std_per_round_units": 1.1}}]}


def test_build_dashboard_inlines_sweep_and_betspread(tmp_path):
    out = tmp_path / "dashboard.html"
    betspread_payload = [{"penetration": 0.75, "rules_name": "6D S17 DAS noLS 3:2",
                          "breakeven_top": 3.8, "rounds_each": 1000, "top_tc": 5,
                          "results": [{"top": 1, "total_house_edge": 0.003,
                                       "std_per_round_units": 1.1}]}]
    visualize.build_dashboard(_fake_sweep(), visualize.TEMPLATE, str(out),
                              betspread=betspread_payload)
    html = out.read_text()
    # Markers must be fully replaced with valid JSON, none left behind.
    assert "/*__SWEEP_DATA__*/" not in html
    assert "/*__BETSPREAD_DATA__*/" not in html
    assert "6D S17 DAS noLS 3:2" in html
    # The inlined betspread blob should parse back to our payload.
    import re
    m = re.search(r"const BETSPREAD_DATA = (\[.*?\]);", html)
    assert json.loads(m.group(1))[0]["breakeven_top"] == 3.8


# --- strategy chart generator ------------------------------------------------
def test_strategy_chart_neutral_count_has_no_deviations():
    """At true count 0 the chart must equal basic strategy for every cell."""
    rules = Rules()                                   # 6D S17 DAS LS 3:2
    tc_grid = list(range(-3, 6))
    models = [EVModel(float(tc)) for tc in tc_grid]
    i0 = tc_grid.index(0)
    for total in range(5, 21):
        for up in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
            cell = sc.cell_for(sc.hard_cards(total) if total <= 17 else
                               [sc.card_for_value(10), sc.card_for_value(total - 10)],
                               up, False, rules, tc_grid, models)
            assert cell["acts"][i0] == cell["basic"], f"{total}v{up} deviates at TC 0"


def test_strategy_chart_cells_match_engine_deviations():
    rules = Rules()                                   # 6D S17 DAS LS 3:2
    tc_grid = list(range(-3, 6))
    models = [EVModel(float(tc)) for tc in tc_grid]

    # 16 vs 10: surrender beats the I18 "stand" deviation under LS, so it
    # surrenders at every count (no deviation from basic).
    c16 = sc.cell_for(sc.hard_cards(16), 10, False, rules, tc_grid, models)
    assert c16["basic"] == "R"
    assert all(a == "R" for a in c16["acts"])
    assert len(c16["ev"]["stand"]) == len(tc_grid)

    # 12 vs 4: basic stands; the index play is to HIT below neutral (TC <= -1),
    # NOT at TC 0.
    c124 = sc.cell_for(sc.hard_cards(12), 4, False, rules, tc_grid, models)
    assert c124["basic"] == "S"
    assert c124["acts"][tc_grid.index(0)] == "S"
    assert c124["acts"][tc_grid.index(-1)] == "H"

    # 12 vs 3: basic hits, stands at TC >= 2 (a clean count deviation).
    c123 = sc.cell_for(sc.hard_cards(12), 3, False, rules, tc_grid, models)
    assert c123["basic"] == "H"
    assert c123["acts"][tc_grid.index(2)] == "S"
    assert c123["acts"][tc_grid.index(1)] == "H"


def test_strategy_chart_card_helpers_are_clean_hands():
    from blackjack.cards import RANK_INDEX, hand_total
    for total in range(8, 18):
        a, b = sc.hard_cards(total)
        assert hand_total([a, b])[0] == total
        assert hand_total([a, b])[1] is False          # not soft
        assert RANK_INDEX[a] != RANK_INDEX[b]           # not a pair


def test_build_dashboard_inlines_strategy_chart(tmp_path):
    out = tmp_path / "dashboard.html"
    chart = {"rules_name": "6D S17 DAS LS 3:2", "tc_grid": [-1, 0, 1],
             "upcards": [2, 3], "insurance_index": 3, "insurance_take_at": [0, 0, 0],
             "sections": [{"name": "Hard totals", "kind": "hard", "rows": [
                 {"label": "16", "ev_kind": "hard", "split_note": False, "cells": [
                     {"basic": "S", "acts": ["S", "S", "S"],
                      "ev": {"stand": [-0.4, -0.4, -0.4], "hit": [-0.5, -0.5, -0.5],
                             "double": [-1.0, -1.0, -1.0]}}]}]}]}
    visualize.build_dashboard(_fake_sweep(), visualize.TEMPLATE, str(out),
                              strategy_chart=chart)
    html = out.read_text()
    assert "/*__STRATEGY_CHART_DATA__*/" not in html   # marker fully replaced
    import re
    m = re.search(r"const STRATEGY_CHART_DATA = (\{.*?\});", html)
    assert json.loads(m.group(1))["rules_name"] == "6D S17 DAS LS 3:2"


def test_discover_betspread_reads_and_sorts(tmp_path):
    for pen in (0.833, 0.75):
        (tmp_path / f"betspread_{int(pen*1000)}.json").write_text(
            json.dumps({"penetration": pen,
                        "results": [{"top": 1, "total_house_edge": 0.003}]}))
    found = visualize.discover_betspread(str(tmp_path), None)
    assert [d["penetration"] for d in found] == [0.75, 0.833]   # sorted shallow->deep
