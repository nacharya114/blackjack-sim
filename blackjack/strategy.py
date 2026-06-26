"""Decision logic: basic strategy, late surrender, and Hi-Lo index deviations.

Action codes used inside the tables:
    H  hit
    S  stand
    D  double if allowed else hit
    V  double if allowed else stand   (a.k.a. "Ds")
    P  split
    R  surrender if allowed else hit

Column order for every 10-character table string is dealer upcard:
    index 0..9  ->  2 3 4 5 6 7 8 9 10 A
"""

from __future__ import annotations

from .cards import hand_total, RANK_INDEX, BJ_VALUE
from .rules import Rules

# Map a dealer upcard value (2..11, Ace=11) to a column index 0..9.
def _col(up_value: int) -> int:
    return 9 if up_value == 11 else up_value - 2


# --- Hard totals (4-8 deck, S17 baseline) -------------------------------------
#                       2    3    4    5    6    7    8    9    10   A
BASIC_HARD = {
    4:  "HHHHHHHHHH",
    5:  "HHHHHHHHHH", 6:  "HHHHHHHHHH", 7: "HHHHHHHHHH", 8: "HHHHHHHHHH",
    9:  "HDDDDHHHHH",
    10: "DDDDDDDDHH",
    11: "DDDDDDDDDH",          # S17: double vs 2-10, hit vs A (H17 override below)
    12: "HHSSSHHHHH",
    13: "SSSSSHHHHH",
    14: "SSSSSHHHHH",
    15: "SSSSSHHHHH",          # surrender vs 10 handled in should_surrender()
    16: "SSSSSHHHHH",          # surrender vs 9,10,A handled in should_surrender()
    17: "SSSSSSSSSS", 18: "SSSSSSSSSS", 19: "SSSSSSSSSS",
    20: "SSSSSSSSSS", 21: "SSSSSSSSSS",
}

# --- Soft totals (4-8 deck, S17 baseline) -------------------------------------
#                       2    3    4    5    6    7    8    9    10   A
BASIC_SOFT = {
    13: "HHHDDHHHHH",          # A,2
    14: "HHHDDHHHHH",          # A,3
    15: "HHDDDHHHHH",          # A,4
    16: "HHDDDHHHHH",          # A,5
    17: "HDDDDHHHHH",          # A,6
    18: "SVVVVSSHHH",          # A,7
    19: "SSSSSSSSSS",          # A,8  (H17 override vs 6 below)
    20: "SSSSSSSSSS",          # A,9
    21: "SSSSSSSSSS",
}

# --- Pairs, with Double-After-Split allowed -----------------------------------
#  keyed by the pair card's rank value (2..11, Ace=11)
#                       2    3    4    5    6    7    8    9    10   A
PAIRS_DAS = {
    2:  "PPPPPPHHHH",
    3:  "PPPPPPHHHH",
    4:  "HHHPPHHHHH",
    5:  "DDDDDDDDHH",          # never split; play as hard 10
    6:  "PPPPPHHHHH",
    7:  "PPPPPPHHHH",
    8:  "PPPPPPPPPP",
    9:  "PPPPPSPPSS",
    10: "SSSSSSSSSS",
    11: "PPPPPPPPPP",          # A,A
}

# --- Pairs, NO Double-After-Split ---------------------------------------------
PAIRS_NODAS = dict(PAIRS_DAS)
PAIRS_NODAS[2] = "HHPPPPHHHH"   # split only vs 4-7
PAIRS_NODAS[3] = "HHPPPPHHHH"
PAIRS_NODAS[4] = "HHHHHHHHHH"   # never split without DAS
PAIRS_NODAS[6] = "HPPPPHHHHH"   # split vs 3-6


def _basic_hard_action(total: int, up: int, rules: Rules) -> str:
    a = BASIC_HARD[total][_col(up)]
    if rules.dealer_hits_soft_17:
        if total == 11 and up == 11:      # H17: double 11 vs Ace
            a = "D"
    return a


def _basic_soft_action(total: int, up: int, rules: Rules) -> str:
    a = BASIC_SOFT[total][_col(up)]
    if rules.dealer_hits_soft_17:
        if total == 19 and up == 6:       # H17: A,8 doubles vs 6
            a = "V"
    return a


def should_surrender(cards, up: int, is_pair: bool, rules: Rules) -> bool:
    """Late-surrender decision for the first two cards (S17 baseline + H17 adds)."""
    if not (rules.late_surrender or rules.early_surrender):
        return False
    total, soft = hand_total(cards)
    if soft:
        return False
    pair_rank = BJ_VALUE[cards[0]] if is_pair else 0
    h17 = rules.dealer_hits_soft_17

    # 8,8 vs Ace surrenders only under H17 (otherwise it is split).
    if is_pair and pair_rank == 8:
        return h17 and up == 11

    if total == 15:
        if up == 10:
            return True
        if up == 11 and h17:
            return True
    if total == 16 and up in (9, 10, 11):
        return True
    if total == 17 and up == 11 and h17:
        return True
    return False


def basic_action(cards, up: int, *, can_double: bool, can_split: bool,
                 can_surrender: bool, rules: Rules) -> str:
    """Return a concrete action: 'H','S','D','P','R'.

    'D' here already means "double now" (legality checked by caller via can_double);
    if doubling is illegal the table fallback (hit/stand) is returned instead.
    """
    is_pair = len(cards) == 2 and RANK_INDEX[cards[0]] == RANK_INDEX[cards[1]] \
        or (len(cards) == 2 and BJ_VALUE[cards[0]] == 10 and BJ_VALUE[cards[1]] == 10)

    if can_surrender and should_surrender(cards, up, is_pair, rules):
        return "R"

    # Pair logic first.
    if is_pair and can_split:
        rank_val = BJ_VALUE[cards[0]]
        table = PAIRS_DAS if rules.double_after_split else PAIRS_NODAS
        code = table[rank_val][_col(up)]
        if code == "P":
            return "P"
        # otherwise fall through to total-based play using the resolved code
        if code in ("D", "V"):
            return code if can_double else ("H" if code == "D" else "S")
        return code  # 'H' or 'S'

    total, soft = hand_total(cards)
    if soft and total <= 21:
        code = _basic_soft_action(total, up, rules)
    else:
        code = _basic_hard_action(min(total, 21), up, rules)

    if code == "D":
        return "D" if can_double else "H"
    if code == "V":
        return "D" if can_double else "S"
    if code == "R":
        return "R" if can_surrender else "H"
    return code  # 'H' or 'S'


# --- Hi-Lo index deviations (Illustrious 18 + insurance) ----------------------
# Each entry: (total, is_soft, dealer_up, deviate_action, threshold, direction)
# direction '>=' : use deviate_action when true_count >= threshold
# direction '<=' : use deviate_action when true_count <= threshold
ILLUSTRIOUS_18 = [
    (16, False, 10, "S", 0, ">="),
    (15, False, 10, "S", 4, ">="),
    (12, False, 3,  "S", 2, ">="),
    (12, False, 2,  "S", 3, ">="),
    (11, False, 11, "D", 1, ">="),
    (9,  False, 2,  "D", 1, ">="),
    (10, False, 10, "D", 4, ">="),
    (10, False, 11, "D", 4, ">="),
    (9,  False, 7,  "D", 3, ">="),
    (16, False, 9,  "S", 5, ">="),
    # 13v2 and 12v4 are stand-by-basic hands whose index play is to HIT at deeply
    # negative counts (like 12v5/12v6/13v3 below), so they use direction "<=".
    (13, False, 2,  "H", -1, "<="),
    (12, False, 4,  "H", 0,  "<="),
    (12, False, 5,  "H", -2, "<="),
    (12, False, 6,  "H", -1, "<="),
    (13, False, 3,  "H", -2, "<="),
]


def counter_action(cards, up: int, true_count: float, *, can_double: bool,
                   can_split: bool, can_surrender: bool, rules: Rules) -> str:
    """Basic strategy modified by Hi-Lo index plays."""
    total, soft = hand_total(cards)
    is_pair = len(cards) == 2 and RANK_INDEX[cards[0]] == RANK_INDEX[cards[1]]
    if not is_pair:  # deviations below are for non-pair totals
        for t, s, u, act, thr, direction in ILLUSTRIOUS_18:
            if t == total and s == soft and u == up:
                hit = true_count >= thr if direction == ">=" else true_count <= thr
                if hit:
                    if act == "D":
                        return "D" if can_double else "H"
                    return act
    return basic_action(cards, up, can_double=can_double, can_split=can_split,
                        can_surrender=can_surrender, rules=rules)


def take_insurance(true_count: float) -> bool:
    """Counter takes insurance at true count >= 3 (Hi-Lo)."""
    return true_count >= 3.0
