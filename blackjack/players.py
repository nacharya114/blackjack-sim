"""Player strategies. Each exposes:

    bet(true_count) -> units to wager on the main bet (>=0)
    play(cards, up, *, can_double, can_split, can_surrender, true_count, rules) -> action
    insurance(true_count) -> bool
    side_bets : tuple of active side-bet names
    side_bet_unit : units staked on each side bet per round
"""

from __future__ import annotations

from . import strategy as strat


class BasicStrategy:
    """Flat better who follows basic strategy and never takes insurance."""

    def __init__(self, bet_units: float = 1.0, side_bets=(), side_bet_unit: float = 1.0):
        self.name = "basic"
        self._bet = bet_units
        self.side_bets = tuple(side_bets)
        self.side_bet_unit = side_bet_unit

    def bet(self, true_count: float) -> float:
        return self._bet

    def play(self, cards, up, *, can_double, can_split, can_surrender, true_count, rules):
        return strat.basic_action(cards, up, can_double=can_double, can_split=can_split,
                                  can_surrender=can_surrender, rules=rules)

    def insurance(self, true_count: float) -> bool:
        return False


class CardCounter:
    """Hi-Lo counter: ramps bets by true count and uses index deviations.

    bet_ramp maps the floor of the true count to a bet in units. Counts below the
    smallest key use min_bet; counts at or above the largest key use its value.
    """

    DEFAULT_RAMP = {1: 1, 2: 2, 3: 4, 4: 8, 5: 12}

    def __init__(self, bet_ramp: dict | None = None, min_bet: float = 1.0,
                 side_bets=(), side_bet_unit: float = 1.0):
        self.name = "counter"
        self.ramp = bet_ramp or dict(self.DEFAULT_RAMP)
        self.min_bet = min_bet
        self._max_key = max(self.ramp)
        self.side_bets = tuple(side_bets)
        self.side_bet_unit = side_bet_unit

    def bet(self, true_count: float) -> float:
        tc = int(true_count // 1)  # floor
        if tc < min(self.ramp):
            return self.min_bet
        if tc >= self._max_key:
            return float(self.ramp[self._max_key])
        return float(self.ramp.get(tc, self.min_bet))

    def play(self, cards, up, *, can_double, can_split, can_surrender, true_count, rules):
        return strat.counter_action(cards, up, true_count, can_double=can_double,
                                    can_split=can_split, can_surrender=can_surrender,
                                    rules=rules)

    def insurance(self, true_count: float) -> bool:
        return strat.take_insurance(true_count)


class WindowCounter:
    """Counts the *buffer* of a windowed CSM and bets up when it is low-card rich.

    On a windowed CSM the last `csm_buffer` dealt cards are held out of the
    dealing pool, so the Hi-Lo count of that buffer (which `Shoe.true_count()`
    returns directly in windowed mode -- no divisor) tells you how ten-rich the
    pool is. Following discountgambling.net, this flat-bets the minimum until the
    buffer count reaches the ramp, then bets up; it plays **basic strategy**
    (there is no shoe count to take index plays on).

    `bet_ramp` maps a buffer count to a bet; counts below the smallest key bet
    `min_bet`, at/above the largest key bet its value.
    """

    DEFAULT_RAMP = {5: 4, 6: 6, 7: 8, 8: 10, 9: 12}

    def __init__(self, bet_ramp: dict | None = None, min_bet: float = 1.0,
                 insurance_at: int | None = None, side_bets=(), side_bet_unit: float = 1.0):
        self.name = "window_counter"
        self.ramp = bet_ramp or dict(self.DEFAULT_RAMP)
        self.min_bet = min_bet
        self._min_key = min(self.ramp)
        self._max_key = max(self.ramp)
        self.insurance_at = insurance_at      # buffer count to start taking insurance
        self.side_bets = tuple(side_bets)
        self.side_bet_unit = side_bet_unit

    def bet(self, true_count: float) -> float:
        wc = int(true_count)                  # the buffer count (already an integer)
        if wc < self._min_key:
            return self.min_bet
        if wc >= self._max_key:
            return float(self.ramp[self._max_key])
        return float(self.ramp.get(wc, self.min_bet))

    def play(self, cards, up, *, can_double, can_split, can_surrender, true_count, rules):
        return strat.basic_action(cards, up, can_double=can_double, can_split=can_split,
                                  can_surrender=can_surrender, rules=rules)

    def insurance(self, true_count: float) -> bool:
        return self.insurance_at is not None and true_count >= self.insurance_at


def build_strategy(name: str, **kwargs):
    name = name.lower()
    if name in ("basic", "basic_strategy", "flat"):
        return BasicStrategy(**kwargs)
    if name in ("counter", "card_counter", "hilo"):
        return CardCounter(**kwargs)
    if name in ("window_counter", "window", "csm_counter"):
        return WindowCounter(**kwargs)
    raise ValueError(f"unknown strategy: {name}")
