"""Blackjack EV & house-edge simulator."""

from .rules import Rules, PRESETS
from .shoe import Shoe
from .players import BasicStrategy, CardCounter, build_strategy
from .game import play_round, RoundResult
from .simulator import run_simulation
from .sidebets import SIDE_BETS, DEFAULT_PERFECT_PAIRS, DEFAULT_21P3

__all__ = [
    "Rules", "PRESETS", "Shoe", "BasicStrategy", "CardCounter", "build_strategy",
    "play_round", "RoundResult", "run_simulation", "SIDE_BETS",
    "DEFAULT_PERFECT_PAIRS", "DEFAULT_21P3",
]
