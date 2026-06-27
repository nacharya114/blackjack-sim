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


class _CFallback(Exception):
    """Raised when a config can't be handled by the native engine (caller falls back)."""


def fast_available() -> bool:
    """True if the native (Cython) acceleration extension is importable."""
    try:
        from . import _fastsim  # noqa: F401
        return True
    except ImportError:
        return False


def _native_unsupported_reason(rules: Rules, paytables) -> str | None:
    """Why the native engine can't run this config (None = it can). Checked up
    front so `auto` falls back and forced `c` errors clearly, before any rounds."""
    if paytables:
        return "custom side-bet paytables"
    if rules.decks > 8:
        return "more than 8 decks"
    return None


def _build_c_rules(rules: Rules):
    if rules.decks > 8:
        raise _CFallback("native engine supports up to 8 decks")
    cut = int(rules.decks * 52 * (1.0 - rules.penetration))
    return (int(rules.decks), cut, int(rules.dealer_hits_soft_17), int(rules.dealer_peeks),
            float(rules.blackjack_payout), int(rules.double_allowed),
            int(rules.double_after_split), int(rules.double_totals[0]),
            int(rules.double_totals[1]), int(rules.max_split_hands),
            int(rules.resplit_aces), int(rules.hit_split_aces),
            int(rules.late_surrender), int(rules.early_surrender), int(rules.csm))


def _build_c_strat(strategy):
    side_bets = set(getattr(strategy, "side_bets", ()))
    if side_bets - {"perfect_pairs", "21+3"}:
        raise _CFallback("native engine only supports perfect_pairs / 21+3 side bets")
    pp = 1 if "perfect_pairs" in side_bets else 0
    tp = 1 if "21+3" in side_bets else 0
    su = float(getattr(strategy, "side_bet_unit", 1.0))
    if getattr(strategy, "name", "") == "counter":
        ramp = strategy.ramp
        min_key, max_key = min(ramp), max(ramp)
        if max_key - min_key >= 64:
            raise _CFallback("counter ramp span too wide for native engine")
        dense = [float(ramp.get(k, strategy.min_bet)) for k in range(min_key, max_key + 1)]
        c_strat = (1, 0.0, float(strategy.min_bet), int(min_key), int(max_key),
                   dense, 1, pp, tp, su)
    else:
        bet_units = float(getattr(strategy, "_bet", 1.0))
        c_strat = (0, bet_units, 1.0, 0, 0, [], 0, pp, tp, su)
    return c_strat, pp, tp


def _run_c(rules: Rules, strategy_name, strategy_kwargs, rounds, base_seed, cores) -> _Partial:
    """Run the whole simulation through the native engine (threads release the GIL)."""
    from . import _fastsim
    strategy = build_strategy(strategy_name, **strategy_kwargs)
    c_rules = _build_c_rules(rules)
    c_strat, pp, tp = _build_c_strat(strategy)
    mask = (1 << 64) - 1

    def one(n_rounds, seed):
        return _fastsim.simulate_chunk(c_rules, c_strat, int(n_rounds), seed & mask)

    if cores == 1:
        outs = [one(rounds, base_seed)]
    else:
        per = rounds // cores
        chunks = [per] * cores
        chunks[-1] += rounds - per * cores
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=cores) as ex:
            outs = list(ex.map(lambda a: one(*a),
                               [(c, base_seed + i) for i, c in enumerate(chunks)]))

    p = _Partial()
    for out in outs:
        (rnds, mn, mnsq, mw, ib, tn, tnsq, tw, sn, sw, ppn, ppw, tpn, tpw) = out
        p.rounds += rnds
        p.main_net += mn
        p.main_net_sq += mnsq
        p.main_wagered += mw
        p.initial_bet += ib
        p.total_net += tn
        p.total_net_sq += tnsq
        p.total_wagered += tw
        p.side_net += sn
        p.side_wagered += sw
        if pp:
            p.side_net_by["perfect_pairs"] = p.side_net_by.get("perfect_pairs", 0.0) + ppn
            p.side_wagered_by["perfect_pairs"] = p.side_wagered_by.get("perfect_pairs", 0.0) + ppw
        if tp:
            p.side_net_by["21+3"] = p.side_net_by.get("21+3", 0.0) + tpn
            p.side_wagered_by["21+3"] = p.side_wagered_by.get("21+3", 0.0) + tpw
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
                   seed: int | None = None, engine: str = "python") -> dict:
    """Run `rounds` rounds and return a results dict (JSON-serialisable).

    `engine` selects the round engine:
        "python" (default) -- the pure-stdlib reference engine.
        "c"/"cython"/"fast" -- the native extension; errors if it isn't built.
        "auto" -- use the native engine when it's built and the config is
                  supported, otherwise fall back to Python.
    The native engine produces the same expected values but uses a different RNG,
    so it does not reproduce the Python engine bit-for-bit for a given seed.
    """
    strategy_kwargs = strategy_kwargs or {}
    rules_dict = rules.to_dict()
    base_seed = seed if seed is not None else random.randrange(1 << 30)
    cores = max(1, int(cores))

    engine = (engine or "python").lower()
    use_c = False
    if engine in ("c", "cython", "fast", "native"):
        if not fast_available():
            raise RuntimeError(
                "native engine not built. Build it with `make build` "
                "(or `python setup.py build_ext --inplace`), or use engine='python'.")
        reason = _native_unsupported_reason(rules, paytables)
        if reason:
            raise ValueError(
                f"native engine can't handle {reason}; use engine='python' or 'auto'.")
        use_c = True
    elif engine == "auto":
        use_c = fast_available() and _native_unsupported_reason(rules, paytables) is None

    used_engine = "python"
    if use_c:
        try:
            partial = _run_c(rules, strategy_name, strategy_kwargs, rounds, base_seed, cores)
            used_engine = "c"
        except _CFallback as exc:
            if engine != "auto":
                raise ValueError(
                    f"native engine can't handle this config: {exc}; "
                    "use engine='python' or 'auto'.") from None
            use_c = False

    if not use_c:
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
        "engine": used_engine,
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
