"""Resolve a single round of blackjack and return its net result in units.

The engine plays one player seat heads-up against the dealer. It returns a
RoundResult capturing the main-bet outcome, side-bet outcome, and amounts
wagered, which the simulator aggregates into EV and house-edge statistics.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cards import hand_total, is_blackjack, BJ_VALUE, IS_ACE, IS_TEN, RANK_INDEX
from .rules import Rules
from .shoe import Shoe
from .sidebets import resolve_side_bets


@dataclass
class RoundResult:
    main_net: float          # net units won/lost on main + double + split + surrender + insurance
    main_wagered: float      # total units placed on main bets this round (incl doubles/splits/insurance)
    initial_bet: float       # the base wager placed before any double/split (house-edge denominator)
    side_net: float          # net units on side bets
    side_wagered: float      # total units placed on side bets
    side_breakdown: dict     # {bet_name: net} for attribution


def _can_double(cards, from_split, is_split_aces, rules: Rules) -> bool:
    if len(cards) != 2:
        return False
    total = hand_total(cards)[0]
    if not rules.can_double_total(total):
        return False
    if from_split and not rules.double_after_split:
        return False
    if is_split_aces and not rules.hit_split_aces:
        return False
    return True


def _play_one_hand(shoe: Shoe, cards, bet, up_value, rules, strategy,
                   from_split, is_split_aces, allow_surrender):
    """Play a single (already-formed) hand to completion.

    Returns dict with keys: cards, bet, status in {'stand','bust','surrender','double'}.
    """
    status = "stand"
    while True:
        total, soft = hand_total(cards)
        if total > 21:
            status = "bust"
            break
        if is_split_aces and not rules.hit_split_aces:
            status = "stand"
            break

        can_dbl = _can_double(cards, from_split, is_split_aces, rules)
        can_sur = allow_surrender and len(cards) == 2 and not from_split
        tc = shoe.true_count()
        action = strategy.play(cards, up_value, can_double=can_dbl, can_split=False,
                               can_surrender=can_sur, true_count=tc, rules=rules)

        if action == "R" and can_sur:
            status = "surrender"
            break
        if action == "D" and can_dbl:
            cards.append(shoe.deal())
            bet *= 2
            status = "double" if hand_total(cards)[0] <= 21 else "bust"
            break
        if action == "S":
            status = "stand"
            break
        # default: hit
        cards.append(shoe.deal())

    return {"cards": cards, "bet": bet, "status": status}


def _play_player(shoe: Shoe, c0, c1, base_bet, up_value, rules, strategy):
    """Handle splitting then play out every resulting hand. Returns list of hand dicts."""
    # Decide split at the top using the strategy.
    resolved = []
    # stack of hands waiting to be played: (cards, bet, from_split, is_split_aces)
    pending = [([c0, c1], base_bet, False, False)]
    n_hands = 1
    allow_surrender = rules.late_surrender or rules.early_surrender

    while pending:
        cards, bet, from_split, is_split_aces = pending.pop()
        # Can this hand be split?
        is_pair = (RANK_INDEX[cards[0]] == RANK_INDEX[cards[1]]) or \
                  (BJ_VALUE[cards[0]] == 10 and BJ_VALUE[cards[1]] == 10)
        can_split = (is_pair and n_hands < rules.max_split_hands and len(cards) == 2)
        if is_split_aces and not rules.resplit_aces:
            can_split = False

        if can_split:
            tc = shoe.true_count()
            can_dbl = _can_double(cards, from_split, is_split_aces, rules)
            can_sur = allow_surrender and not from_split
            action = strategy.play(cards, up_value, can_double=can_dbl, can_split=True,
                                   can_surrender=can_sur, true_count=tc, rules=rules)
            if action == "P":
                splitting_aces = IS_ACE[cards[0]] == 1
                # form two hands, one card to each
                h1 = [cards[0], shoe.deal()]
                h2 = [cards[1], shoe.deal()]
                n_hands += 1
                # push both back for potential resplit / play
                pending.append((h2, base_bet, True, splitting_aces))
                pending.append((h1, base_bet, True, splitting_aces))
                continue

        resolved.append(_play_one_hand(shoe, cards, bet, up_value, rules, strategy,
                                       from_split, is_split_aces, allow_surrender))
    return resolved


def play_round(shoe: Shoe, rules: Rules, strategy, paytables=None) -> RoundResult:
    # A continuous shuffle machine reshuffles every round (a full N-deck stack), so
    # the count never builds. Otherwise reshuffle at the cut card, or if too few
    # cards remain to safely finish a round.
    if rules.csm or shoe.needs_shuffle() or shoe.cards_remaining < 20:
        shoe.shuffle()

    tc_start = shoe.true_count()
    main_bet = strategy.bet(tc_start)
    side_active = set(getattr(strategy, "side_bets", ()))
    side_unit = getattr(strategy, "side_bet_unit", 1.0)

    # Deal: player two cards, dealer up, dealer hole (hidden).
    p0 = shoe.deal()
    d_up = shoe.deal()
    p1 = shoe.deal()
    hole = shoe.deal_hidden()
    up_value = BJ_VALUE[d_up]

    # --- side bets (depend only on player's two cards + dealer up) ---
    side_breakdown = resolve_side_bets([p0, p1], d_up, side_active, paytables)
    side_net = sum(v * side_unit for v in side_breakdown.values())
    side_wagered = len(side_breakdown) * side_unit

    main_net = 0.0
    main_wagered = 0.0

    player_cards = [p0, p1]
    player_bj = is_blackjack(player_cards)
    dealer_up_is_ace = IS_ACE[d_up] == 1
    dealer_up_is_ten = IS_TEN[d_up] == 1

    # --- insurance + dealer peek ---
    insurance_net = 0.0
    took_insurance = dealer_up_is_ace and strategy.insurance(tc_start)
    if took_insurance:
        ins_bet = 0.5 * main_bet
        # insurance wagered tracked within main wagered denominator
        main_wagered += ins_bet

    dealer_has_bj = False
    if rules.dealer_peeks and (dealer_up_is_ace or dealer_up_is_ten):
        if is_blackjack([d_up, hole]):
            dealer_has_bj = True

    if dealer_has_bj:
        shoe.reveal(hole)  # hole now public
        if took_insurance:
            insurance_net = +2.0 * (0.5 * main_bet)   # pays 2:1 on the insurance stake
        # main bet: push if player also has BJ, else loss
        main_wagered += main_bet
        if player_bj:
            main_net += 0.0
        else:
            main_net += -main_bet
        main_net += insurance_net
        return RoundResult(main_net, main_wagered, main_bet, side_net, side_wagered, side_breakdown)

    # dealer does not have blackjack
    if took_insurance:
        insurance_net = -0.5 * main_bet
        main_net += insurance_net

    if player_bj:
        # peek already ruled out dealer BJ -> immediate win at payout
        main_wagered += main_bet
        main_net += rules.blackjack_payout * main_bet
        return RoundResult(main_net, main_wagered, main_bet, side_net, side_wagered, side_breakdown)

    # --- player plays out (with possible splits) ---
    hands = _play_player(shoe, p0, p1, main_bet, up_value, rules, strategy)
    for h in hands:
        main_wagered += h["bet"]

    # --- dealer plays if any hand needs a showdown ---
    need_showdown = any(h["status"] in ("stand", "double") for h in hands)
    shoe.reveal(hole)
    dealer_cards = [d_up, hole]
    if need_showdown:
        while True:
            dt, dsoft = hand_total(dealer_cards)
            if dt < 17:
                dealer_cards.append(shoe.deal())
                continue
            if dt == 17 and dsoft and rules.dealer_hits_soft_17:
                dealer_cards.append(shoe.deal())
                continue
            break
    dealer_total = hand_total(dealer_cards)[0]
    dealer_bust = dealer_total > 21

    # --- settle each hand ---
    for h in hands:
        bet = h["bet"]
        st = h["status"]
        if st == "surrender":
            main_net += -0.5 * bet
        elif st == "bust":
            main_net += -bet
        else:  # stand or double
            ptot = hand_total(h["cards"])[0]
            if dealer_bust or ptot > dealer_total:
                main_net += bet
            elif ptot < dealer_total:
                main_net += -bet
            # else push

    return RoundResult(main_net, main_wagered, main_bet, side_net, side_wagered, side_breakdown)
