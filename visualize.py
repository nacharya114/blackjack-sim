#!/usr/bin/env python3
"""Build the offline dashboard (and optional PNG charts) from a sweep JSON file.

Usage:
    python visualize.py --data results/sweep.json
    python visualize.py --data results/sweep.json --no-png   # dashboard only

The dashboard is a single self-contained HTML file: the sweep data is inlined
directly into the page, so it opens from disk with a double-click -- no server,
no internet, no CORS headaches.
"""
import argparse
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "dashboard_template.html")
MARKER = "/*__SWEEP_DATA__*/ null"
MARKER_BS = "/*__BETSPREAD_DATA__*/ null"
MARKER_ST = "/*__STRATEGY_DATA__*/ null"


def load(path):
    with open(path) as f:
        return json.load(f)


def build_dashboard(payload, template_path, out_path, betspread=None, strategy=None):
    with open(template_path) as f:
        html = f.read()
    if MARKER not in html:
        raise SystemExit(f"template marker not found in {template_path}")
    # json.dumps is valid JS-object literal syntax; inline it so file:// works.
    html = html.replace(MARKER, json.dumps(payload, separators=(",", ":")), 1)
    # Optional bet-spread panel data (a list of per-penetration payloads).
    html = html.replace(MARKER_BS, json.dumps(betspread or [], separators=(",", ":")), 1)
    # Optional strategy-deviation panel data.
    html = html.replace(MARKER_ST, json.dumps(strategy, separators=(",", ":")), 1)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def discover_betspread(out_dir, explicit):
    """Resolve bet-spread JSON files: explicit list, else auto-discover."""
    paths = explicit if explicit is not None else sorted(
        glob.glob(os.path.join(out_dir, "betspread_*.json")))
    out = []
    for p in paths:
        try:
            out.append(load(p))
        except Exception as e:                                    # pragma: no cover
            print(f"  (skipping bet-spread file {p}: {e})")
    # Sort panels by penetration so the dropdown reads shallow -> deep.
    out.sort(key=lambda d: d.get("penetration", 0))
    return out


# ---------------------------------------------------------------- PNG charts --
def make_pngs(payload, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                                    # pragma: no cover
        print(f"  (matplotlib unavailable -- skipping PNGs: {e})")
        return []

    FELT = "#0b3d2e"
    PANEL = "#0f3b2c"
    GOOD = "#5fcf9a"
    BAD = "#e2585b"
    BRASS = "#c9a227"
    INK = "#e8efe9"

    plt.rcParams.update({
        "figure.facecolor": FELT, "axes.facecolor": PANEL,
        "savefig.facecolor": FELT, "text.color": INK,
        "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
        "axes.edgecolor": "#2c5c49", "font.size": 10,
    })

    results = payload["results"]
    dpu = payload.get("dollars_per_unit", 10.0)
    written = []

    def bars(values, labels, colors, title, xlabel, fname, fmt):
        n = len(values)
        fig, ax = plt.subplots(figsize=(9, 0.55 * n + 1.6))
        ypos = range(n)
        ax.barh(list(ypos), values, color=colors, edgecolor="#08281d")
        ax.set_yticks(list(ypos))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.axvline(0, color="#7fffd4", lw=1, alpha=.5)
        ax.set_xlabel(xlabel)
        ax.set_title(title, color=BRASS, fontweight="bold", pad=12)
        ax.grid(axis="x", color="#ffffff", alpha=.08)
        for y, v in zip(ypos, values):
            ax.text(v + (max(map(abs, values)) * 0.01 * (1 if v >= 0 else -1)),
                    y, fmt(v), va="center",
                    ha="left" if v >= 0 else "right", fontsize=9)
        fig.tight_layout()
        path = os.path.join(out_dir, fname)
        fig.savefig(path, dpi=130)
        plt.close(fig)
        written.append(path)
        return path

    # 1) house edge of the MAIN game, per configuration --------------------
    main = [r for r in results]
    labels = [r.get("label", r["rules_name"]) for r in main]
    he = [r["main"]["house_edge"] * 100 for r in main]
    colors = [GOOD if v < 0 else BAD for v in he]
    bars(he, labels, colors,
         "Main-game house edge by configuration (lower / greener is better)",
         "house edge  (%)  — negative = player advantage",
         "house_edge_by_ruleset.png",
         lambda v: f"{v:+.3f}%")

    # 2) hourly EV in dollars at the sweep's $/unit -------------------------
    hourly = []
    for r in main:
        ev_round = r["total"]["ev_per_round_units"]
        hph = r.get("hands_per_hour", 100)
        hourly.append(ev_round * hph * dpu)
    colors = [GOOD if v >= 0 else BAD for v in hourly]
    bars(hourly, labels, colors,
         f"Hourly EV  (main + side bets, ${dpu:.0f}/unit)",
         "expected $ / hour",
         "hourly_ev.png",
         lambda v: ("+$" if v >= 0 else "-$") + f"{abs(v):.2f}")

    # 3) side-bet house edges (only runs that carry side bets) --------------
    sb_labels, sb_vals = [], []
    for r in main:
        for name, info in (r.get("side_bets") or {}).items():
            sb_labels.append(f"{name}  [{r.get('label', r['rules_name'])}]")
            sb_vals.append(info["house_edge"] * 100)
    if sb_vals:
        colors = [BAD] * len(sb_vals)  # side bets are always a house win
        bars(sb_vals, sb_labels, colors,
             "Side-bet house edge",
             "house edge  (%)",
             "sidebet_house_edge.png",
             lambda v: f"{v:+.2f}%")
    else:
        print("  (no side-bet runs in this sweep -- skipping sidebet chart)")

    return written


def main():
    ap = argparse.ArgumentParser(description="Build the blackjack dashboard from a sweep file")
    ap.add_argument("--data", default="results/sweep.json", help="sweep JSON produced by run_sim.py")
    ap.add_argument("--template", default=TEMPLATE)
    ap.add_argument("--out", default=None, help="output HTML (default: alongside the data file)")
    ap.add_argument("--no-png", action="store_true", help="skip the static PNG charts")
    ap.add_argument("--betspread", nargs="*", default=None,
                    help="bet-spread JSON file(s) from betspread.py for the spread panel; "
                         "default: auto-discover results/betspread_*.json next to --data")
    ap.add_argument("--strategy", default=None,
                    help="strategy-deviation JSON from strategy_ev.py; "
                         "default: results/strategy_ev.json next to --data if present")
    args = ap.parse_args()

    payload = load(args.data)
    out_dir = os.path.dirname(os.path.abspath(args.data))
    out_html = args.out or os.path.join(out_dir, "dashboard.html")

    betspread = discover_betspread(out_dir, args.betspread)
    strat_path = args.strategy or os.path.join(out_dir, "strategy_ev.json")
    strategy = load(strat_path) if os.path.exists(strat_path) else None
    build_dashboard(payload, args.template, out_html, betspread=betspread, strategy=strategy)
    extras = []
    if betspread:
        extras.append(f"{len(betspread)} bet-spread panel(s)")
    if strategy:
        extras.append("strategy panel")
    print(f"  Wrote {out_html}" + (f"  (+{', '.join(extras)})" if extras else ""))

    if not args.no_png:
        for p in make_pngs(payload, out_dir):
            print(f"  Wrote {p}")

    print("\n  Open the dashboard by double-clicking it (works fully offline).")


if __name__ == "__main__":
    main()
