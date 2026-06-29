"""Analytic, count-adjusted EV of each player action (for the strategy panel).

Infinite-deck, total-dependent EVs for a hard player total versus a dealer
upcard, as a function of the Hi-Lo true count. Used to draw the strategy
panel's action-EV curves; the true count where two actions' EV lines cross is
the index number for that deviation.

Composition model
-----------------
A Hi-Lo true count `tc` means the expected Hi-Lo tag of the next card is exactly
`-tc/52` -- an identity, independent of how many decks remain (the running count
of the *seen* cards is `+RC`, the unseen cards therefore sum to `-RC`, and
`-RC / (52 * decks_remaining) = -tc/52`). We realise that on an infinite deck by
moving probability mass `m = tc/104` from the low group (2-6) to the high group
(tens & aces):

    p(rank 2..6) = 1/13 - m/5
    p(rank 7..9) = 1/13                 # neutral, unchanged
    p(ten group) = 4/13 + 0.8*m         # 10, J, Q, K
    p(ace)       = 1/13 + 0.2*m

Hitting is assumed to play optimally thereafter (standard total-dependent EV).
`action_evs` covers stand / hit / double / surrender for hard *and* soft totals;
passing `pair=` adds the EV of **splitting** that pair (`split_ev`), so the
strategy-chart panel can show every action's EV for pair rows too.
"""
from __future__ import annotations


def densities(tc: float) -> dict[int, float]:
    """Card-value densities (value 11 = ace, 10 = any ten) at true count `tc`."""
    m = tc / 104.0
    p = {v: 1 / 13 for v in range(2, 10)}      # 2..9
    for v in range(2, 7):                       # 2..6 are the low group
        p[v] = 1 / 13 - m / 5
    p[10] = 4 / 13 + 0.8 * m
    p[11] = 1 / 13 + 0.2 * m
    return p


def add_card(total: int, soft: bool, v: int) -> tuple[int, bool]:
    """Apply a drawn card value to a running (total, soft) hand state."""
    if v == 11:
        if total + 11 <= 21:
            total += 11
            soft = True
        else:
            total += 1
    else:
        total += v
    if total > 21 and soft:        # demote a counted-11 ace to 1
        total -= 10
        soft = False
    return total, soft


class EVModel:
    """Dealer + player EV calculator for one true count."""

    def __init__(self, tc: float, h17: bool = False, peek: bool = True):
        self.p = densities(tc)
        self.h17 = h17
        self.peek = peek
        self._dealer: dict[tuple[int, bool], dict] = {}

    # --- dealer ---
    def _dealer_stands(self, total: int, soft: bool) -> bool:
        if total < 17:
            return False
        if total == 17 and soft and self.h17:
            return False          # H17: hit soft 17
        return True

    def dealer_outcomes(self, total: int, soft: bool) -> dict:
        """Distribution over dealer final outcome: ints 17..21 or 'bust'."""
        key = (total, soft)
        cached = self._dealer.get(key)
        if cached is not None:
            return cached
        if total > 21:
            res = {"bust": 1.0}
        elif self._dealer_stands(total, soft):
            res = {total: 1.0}
        else:
            res = {}
            for v, pv in self.p.items():
                nt, ns = add_card(total, soft, v)
                for o, po in self.dealer_outcomes(nt, ns).items():
                    res[o] = res.get(o, 0.0) + pv * po
        self._dealer[key] = res
        return res

    def dealer_dist_from_up(self, up: int) -> dict:
        """Dealer outcome distribution given an upcard, excluding a natural
        when the rules peek (so it reflects the situations where the player
        actually gets to act)."""
        soft = up == 11
        exclude = None
        if self.peek:
            exclude = 10 if up == 11 else (11 if up == 10 else None)
        norm = sum(pv for v, pv in self.p.items() if v != exclude)
        dist: dict = {}
        for v, pv in self.p.items():
            if v == exclude:
                continue
            nt, ns = add_card(up, soft, v)
            w = pv / norm
            for o, po in self.dealer_outcomes(nt, ns).items():
                dist[o] = dist.get(o, 0.0) + w * po
        return dist

    # --- player ---
    def stand_ev(self, total: int, dist: dict) -> float:
        ev = 0.0
        for o, po in dist.items():
            if o == "bust" or total > o:
                ev += po
            elif total < o:
                ev -= po
        return ev

    def _play_fns(self, dist: dict):
        """Build memoized optimal-`hit` and one-card-`double` EV functions for a
        given dealer distribution (shared by action_evs and split_ev)."""
        hit_memo: dict[tuple[int, bool], float] = {}

        def hit(total: int, sft: bool) -> float:
            k = (total, sft)
            if k in hit_memo:
                return hit_memo[k]
            ev = 0.0
            for v, pv in self.p.items():
                nt, ns = add_card(total, sft, v)
                if nt > 21:
                    ev += pv * -1.0
                else:
                    ev += pv * max(self.stand_ev(nt, dist), hit(nt, ns))
            hit_memo[k] = ev
            return ev

        def double(total: int, sft: bool) -> float:
            ev = 0.0
            for v, pv in self.p.items():
                nt, ns = add_card(total, sft, v)
                ev += pv * (-1.0 if nt > 21 else self.stand_ev(nt, dist))
            return 2.0 * ev

        return hit, double

    def action_evs(self, player_total: int, up: int, soft: bool = False,
                   pair: int | None = None, **split_kwargs) -> dict:
        """EV of stand / hit / double / surrender for a (total, up) situation.

        Pass `pair=<card value>` (11 = aces, 10 = any ten) to also include the
        EV of **splitting** that pair (see `split_ev`); extra keyword args are
        forwarded to `split_ev` (das / max_hands / resplit_aces / hit_split_aces)."""
        dist = self.dealer_dist_from_up(up)
        hit, double = self._play_fns(dist)
        out = {
            "stand": self.stand_ev(player_total, dist),
            "hit": hit(player_total, soft),
            "double": double(player_total, soft),
            "surrender": -0.5,
        }
        if pair is not None:
            out["split"] = self.split_ev(pair, up, **split_kwargs)
        return out

    def split_ev(self, pair_val: int, up: int, *, das: bool = True,
                 max_hands: int = 4, resplit_aces: bool = False,
                 hit_split_aces: bool = False) -> float:
        """EV of splitting a pair of value `pair_val` (11 = aces) vs dealer `up`.

        Both post-split hands are independent on the count-adjusted infinite deck,
        so the EV is `2 ×` the value of one split-card "slot" played optimally
        (hit / stand / double-after-split, or one card only for split aces). A
        two-card 21 after a split is a plain 21, not a paid blackjack.

        Resplits are modelled per lineage up to `max_hands` total hands: each slot
        may resplit independently, a slight overestimate of the global hand cap
        (the error is tiny — resplits need a repeated rank — and only nudges the
        already-dominant split EV of A,A / 8,8 a hair higher)."""
        dist = self.dealer_dist_from_up(up)
        hit, double = self._play_fns(dist)
        is_ace = pair_val == 11
        one_card_only = is_ace and not hit_split_aces
        can_double_after = das and not one_card_only

        def play_slot(total: int, soft: bool) -> float:
            """Optimal EV of a freshly-formed post-split two-card hand."""
            if one_card_only:                      # split aces take a single card
                return self.stand_ev(total, dist)
            opts = [self.stand_ev(total, dist), hit(total, soft)]
            if can_double_after:
                opts.append(double(total, soft))
            return max(opts)

        def slot_ev(n_hands: int) -> float:
            """EV of one split-card slot when `n_hands` are already in play."""
            can_resplit = n_hands < max_hands and (not is_ace or resplit_aces)
            base_total, base_soft = add_card(0, False, pair_val)   # the split card
            ev = 0.0
            for v, pv in self.p.items():
                if v == pair_val and can_resplit:                  # draw a match
                    ev += pv * 2.0 * slot_ev(n_hands + 1)          # this slot resplits
                else:
                    nt, ns = add_card(base_total, base_soft, v)
                    ev += pv * play_slot(nt, ns)
            return ev

        return 2.0 * slot_ev(2)        # the first split makes two hands

    def insurance_ev(self) -> float:
        """EV per unit of the insurance side bet (pays 2:1 if dealer has a ten)."""
        return 3.0 * self.p[10] - 1.0
