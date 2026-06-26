"""Ruleset configuration.

Every table-rule that affects house edge lives here. Defaults describe a
common Las Vegas Strip 6-deck game (S17, DAS, late surrender, 3:2).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Rules:
    # --- Shoe ---
    decks: int = 6
    penetration: float = 0.75          # fraction of the shoe dealt before reshuffle

    # --- Dealer ---
    dealer_hits_soft_17: bool = False  # False = S17 (stand), True = H17 (hit)
    dealer_peeks: bool = True          # US peek for blackjack on A/10 upcard

    # --- Blackjack payout ---
    blackjack_payout: float = 1.5      # 3:2 = 1.5, 6:5 = 1.2, 1:1 = 1.0

    # --- Doubling ---
    double_allowed: bool = True
    double_after_split: bool = True    # DAS
    # Totals on which doubling the first two cards is permitted.
    # Use (2, 21) for "double any two", (9, 11) for Reno, (10, 11) for "10-11 only".
    double_totals: tuple[int, int] = (2, 21)

    # --- Splitting ---
    max_split_hands: int = 4           # total hands allowed after splitting (4 = split to 4)
    resplit_aces: bool = False
    hit_split_aces: bool = False       # usually one card to split aces

    # --- Surrender ---
    late_surrender: bool = True        # surrender after dealer checks for BJ
    early_surrender: bool = False      # surrender before dealer checks (rare, player-favourable)

    # --- Misc ---
    hands_per_hour: int = 100          # used to convert per-hand EV into hourly EV

    def can_double_total(self, total: int) -> bool:
        lo, hi = self.double_totals
        return self.double_allowed and lo <= total <= hi

    @property
    def name(self) -> str:
        s17 = "S17" if not self.dealer_hits_soft_17 else "H17"
        das = "DAS" if self.double_after_split else "noDAS"
        ls = "LS" if self.late_surrender else ("ES" if self.early_surrender else "noLS")
        pay = {1.5: "3:2", 1.2: "6:5", 1.0: "1:1"}.get(self.blackjack_payout, f"{self.blackjack_payout}x")
        return f"{self.decks}D {s17} {das} {ls} {pay}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Rules":
        fields = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in fields}
        if "double_totals" in clean:
            clean["double_totals"] = tuple(clean["double_totals"])
        return cls(**clean)


# A few named presets the CLI and dashboard can reference.
PRESETS = {
    "vegas_6deck_s17": Rules(decks=6, dealer_hits_soft_17=False, double_after_split=True,
                             late_surrender=True, blackjack_payout=1.5),
    "vegas_6deck_h17": Rules(decks=6, dealer_hits_soft_17=True, double_after_split=True,
                             late_surrender=False, blackjack_payout=1.5),
    "downtown_2deck_h17": Rules(decks=2, dealer_hits_soft_17=True, double_after_split=True,
                                late_surrender=False, blackjack_payout=1.5),
    "single_deck_s17": Rules(decks=1, dealer_hits_soft_17=False, double_after_split=False,
                             late_surrender=False, blackjack_payout=1.5),
    "bad_6deck_65": Rules(decks=6, dealer_hits_soft_17=True, double_after_split=False,
                          late_surrender=False, blackjack_payout=1.2),
}
