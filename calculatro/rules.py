"""Joker rules that change how hands are detected or how cards are classified.

See docs/jokers.md ("Rules that matter to Calculatro"). The game checks these
with `find_joker`, which skips debuffed jokers, and only cares whether at
least one copy is present; Blueprint and Brainstorm can't copy them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

RULE_JOKERS = {
    "j_four_fingers": "four_fingers",
    "j_shortcut": "shortcut",
    "j_smeared": "smeared",
    "j_pareidolia": "pareidolia",
}


class _Joker(Protocol):
    key: str
    debuff: bool


@dataclass(frozen=True)
class Rules:
    four_fingers: bool = False  # Flushes and Straights need only 4 cards
    shortcut: bool = False  # Straights may skip one rank between cards
    smeared: bool = False  # Hearts/Diamonds and Spades/Clubs are the same suit
    pareidolia: bool = False  # every card is a face card

    @classmethod
    def from_jokers(cls, jokers: Iterable[_Joker]) -> Rules:
        return cls(**{RULE_JOKERS[j.key]: True for j in jokers if j.key in RULE_JOKERS and not j.debuff})
