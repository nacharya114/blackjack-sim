"""Monte Carlo driver: run many rounds, aggregate, and report EV + house edge.

All money figures are tracked in *units* (multiples of one base bet) and only
converted to dollars at the very end using `dollars_per_unit`.

House-edge conventions reported:
    house_edge       = -expected_main_net / total_initial_main_bet   (per original wager)
    element_of_risk  = -expected_main_net / total_main_wagered       (incl. doubles/splits)
A positive value favours the house; a negative value means the player has the edge.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .rules import Rules
from .shoe import Shoe
from .game import play_round
from .players import build_strategy


@dataclass
class _Partial:
    rounds: int = 0
    main_net: float = 0.0
    main_net_sq: float = 0.0
    main_wagered: float = 0.0
    initial_bet: float = 0.0
    total_net: float = 0.0
    total_net_sq: float = 0.0
    total_wagered: float = 0.0
    side_net: float = 0.0
    side_wagered: float = 0.0
    side_net_by: dict = None
    side_wagered_by: dict = None

    def __post_init__(self):
        if self.side_net_by is None:
            self.side_net_by = {}
        if self.side_wagered_by is None:
            self.side_wagered_by = {}


def _simulate_chunk(args) -> _Partial:
    rules_dict, strat_name, strat_kwargs, paytables, n_rounds, seed = args
    rules = Rules.from_dict(rules_dict)
    strategy = build_strategy(strat_name, **strat_kwargs)
    rng = random.Random(seed)
    shoe = Shoe(rules.decks, rules.penetration, rng=rng)

    p = _Partial()
    side_unit = getattr(strategy, "side_bet_unit", 1.0)
    for _ in range(n_rounds):
        r = play_round(shoe, rules, strategy, paytables=paytables)
        total_net = r.main_net + r.side_net
        p.rounds += 1
        p.main_net += r.main_net
        p.main_net_sq += r.main_net * r.main_net
        p.main_wagered += r.main_wagered
        p.initial_bet += r.initial_bet
        p.total_net += total_net
        p.total_net_sq += total_net * total_net
        p.total_wagered += r.main_wagered + r.side_wagered
        p.side_net += r.side_net
        p.side_wagered += r.side_wagered
        for name, net in r.side_breakdown.items():
            p.side_net_by[name] = p.side_net_by.get(name, 0.0) + net * side_unit
            p.side_wagered_by[name] = p.side_wagered_by.get(name, 0.0) + side_unit
    return p


def _merge(partials) -> _Partial:
    out = _Partial()
    for p in partials:
        out.rounds += p.rounds
        out.main_net += p.main_net
        out.main_net_sq += p.main_net_sq
        out.main_wagered += p.main_wagered
        out.initial_bet += p.initial_bet
        out.total_net += p.total_net
        out.total_net_sq += p.total_net_sq
        out.total_wagered += p.total_wagered
        out.side_net += p.side_net
        out.side_wagered += p.side_wagered
        for k, v in p.side_net_by.items():
            out.side_net_by[k] = out.side_net_by.get(k, 0.0) + v
        for k, v in p.side_wagered_by.items():
            out.side_wagered_by[k] = out.side_wagered_by.get(k, 0.0) + v
    return out


def _stdev(sum_x, sum_x2, n):
    if n < 2:
        return 0.0
    mean = sum_x / n
    var = max(0.0, (sum_x2 - n * mean * mean) / (n - 1))
    return math.sqrt(var)


def run_simulation(rules: Rules, strategy_name: str, *, rounds: int = 1_000_000,
                   strategy_kwargs: dict | None = None, paytables: dict | None = None,
                   cores: int = 1, dollars_per_unit: float = 10.0,
                   seed: int | None = None) -> dict:
    """Run `rounds` rounds and return a results dict (JSON-serialisable)."""
    strategy_kwargs = strategy_kwargs or {}
    rules_dict = rules.to_dict()
    base_seed = seed if seed is not None else random.randrange(1 << 30)

    cores = max(1, int(cores))
    if cores == 1:
        partial = _simulate_chunk((rules_dict, strategy_name, strategy_kwargs,
                                   paytables, rounds, base_seed))
    else:
        import multiprocessing as mp
        per = rounds // cores
        chunks = [per] * cores
        chunks[-1] += rounds - per * cores
        jobs = [(rules_dict, strategy_name, strategy_kwargs, paytables, c, base_seed + i)
                for i, c in enumerate(chunks)]
        with mp.Pool(cores) as pool:
            partials = pool.map(_simulate_chunk, jobs)
        partial = _merge(partials)

    n = partial.rounds
    # main bet
    ev_main = partial.main_net / n
    house_edge = -partial.main_net / partial.initial_bet if partial.initial_bet else 0.0
    eor = -partial.main_net / partial.main_wagered if partial.main_wagered else 0.0
    std_main = _stdev(partial.main_net, partial.main_net_sq, n)

    # total (main + side)
    ev_total = partial.total_net / n
    he_total = -partial.total_net / partial.total_wagered if partial.total_wagered else 0.0
    std_total = _stdev(partial.total_net, partial.total_net_sq, n)

    # side bets
    side = {}
    for name, net in partial.side_net_by.items():
        wag = partial.side_wagered_by.get(name, 0.0)
        side[name] = {
            "ev_per_round_units": net / n,
            "house_edge": (-net / wag) if wag else 0.0,
            "rounds_wagered": int(wag / (wag / n)) if wag else 0,  # ~= n if every round
        }

    hph = rules.hands_per_hour
    # standard error of the per-round mean -> hourly CI
    se_round = (std_total / math.sqrt(n)) if n else 0.0

    results = {
        "rules_name": rules.name,
        "rules": rules_dict,
        "strategy": strategy_name,
        "strategy_kwargs": strategy_kwargs,
        "rounds": n,
        "dollars_per_unit": dollars_per_unit,
        "hands_per_hour": hph,
        "main": {
            "ev_per_round_units": ev_main,
            "house_edge": house_edge,                 # per original wager
            "element_of_risk": eor,                   # per total amount risked
            "std_per_round_units": std_main,
            "avg_wager_units": partial.main_wagered / n,
            "avg_initial_units": partial.initial_bet / n,
        },
        "side_bets": side,
        "total": {
            "ev_per_round_units": ev_total,
            "house_edge": he_total,
            "std_per_round_units": std_total,
        },
        "hourly": {
            "ev_units": ev_total * hph,
            "ev_dollars": ev_total * hph * dollars_per_unit,
            "std_dollars": std_total * math.sqrt(hph) * dollars_per_unit,
            "ci95_round_units": 1.96 * se_round,
            "ci95_hour_dollars": 1.96 * se_round * hph * dollars_per_unit,
        },
    }
    return results
