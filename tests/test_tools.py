"""Tests for the dashboard builder and bet-spread helpers (no matplotlib needed)."""
import json


import betspread
import visualize


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


def test_discover_betspread_reads_and_sorts(tmp_path):
    for pen in (0.833, 0.75):
        (tmp_path / f"betspread_{int(pen*1000)}.json").write_text(
            json.dumps({"penetration": pen, "results": []}))
    found = visualize.discover_betspread(str(tmp_path), None)
    assert [d["penetration"] for d in found] == [0.75, 0.833]   # sorted shallow->deep
