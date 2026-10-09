"""Benchmark scenarios for the probability engine.

Each scenario is one odds row as `__main__.odds` computes it: a game state, the
in-game selection to discard, and a target (several targets are combined into
the "Any target" row). States are built like the mod exports them, with
face-down cards hidden in hand and their identities in `hidden_pool`, so the
timings include everything `evaluate_discard` does.

"Unknown cards" are the cards the engine treats as drawn: kept face-down cards
plus the cards drawn after the discard.

"Nothing cached" scenarios time the first odds for a target, as when it's first
tracked or a joker changes the rules: every cache in `calculatro.hands` and
`calculatro.engine` is cleared before each run, so building the target's
patterns is timed too. Their situation keeps the search itself small.

Only APIs that also exist in older versions are used here, so benchmark.py can
time an older checkout (see docs/performance/README.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from threading import Event

import calculatro.engine
import calculatro.hands
from calculatro.engine import evaluate_discard
from calculatro.hands import Target, combine, get_target
from calculatro.state import SUITS, Card, GameState, Joker

RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
_RANKS = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
_SUITS = {s[0]: s for s in SUITS}

# The hand, in order: known cards come first and the last `hidden` are face down.
HAND = ["7H", "8H", "9S", "QC", "KD", "3S", "5D", "JH"]
# Cards turned into Wild Cards (The Lovers) in the "wild" deck; none are in HAND.
WILDS = ["4C", "9D", "JS", "AH"]

JOKERS = {
    "No jokers": (),
    "Four Fingers": ("j_four_fingers",),
    "Shortcut": ("j_shortcut",),
    "Four Fingers + Shortcut": ("j_four_fingers", "j_shortcut"),
    "Smeared": ("j_smeared",),
    "Four Fingers + Smeared": ("j_four_fingers", "j_smeared"),
    "Shortcut + Smeared": ("j_shortcut", "j_smeared"),
    "Four Fingers + Shortcut + Smeared": ("j_four_fingers", "j_shortcut", "j_smeared"),
}


def _parse(text: str) -> tuple[int, str]:
    rank = _RANKS.get(text[:-1]) or int(text[:-1])
    return rank, _SUITS[text[-1]]


def _card(rank: int, suit: str, enhancement: str = "c_base") -> Card:
    return Card(rank=RANK_NAMES.get(rank, str(rank)), rank_id=rank, suit=suit, enhancement=enhancement)


def deck(kind: str) -> list[Card]:
    """A 52-card deck: "standard", or "wild" with WILDS made Wild Cards."""
    wilds = {_parse(w) for w in WILDS} if kind == "wild" else set()
    return [_card(r, s, "m_wild" if (r, s) in wilds else "c_base") for s in SUITS for r in range(2, 15)]


@dataclass(frozen=True)
class Situation:
    label: str
    kept: int  # known hand cards kept; the other known cards are discarded
    hidden: int  # face-down hand cards (kept: they can't be told apart)
    cold: bool = False  # clear the caches before each run (see the module docstring)
    blind: str | None = None  # with "The Mark", the face-down cards are the hand's face cards

    @property
    def unknown(self) -> int:
        return self.hidden + (len(HAND) - self.hidden - self.kept)


KEEP_3 = Situation("Keep 3, draw 5", kept=3, hidden=0)
UNKNOWN_6 = Situation("6 unknown: 1 face-down, draw 5", kept=2, hidden=1)
UNKNOWN_7 = Situation("7 unknown: 2 face-down, draw 5", kept=1, hidden=2)
THE_HOUSE = Situation("8 unknown: The House (all face-down)", kept=0, hidden=8)
COLD_KEEP_3 = Situation("Keep 3, draw 5, nothing cached", kept=3, hidden=0, cold=True)
# The Mark draws face cards face down, so they're HAND's 3 face cards, known to be face cards.
MARK_KEEP_2 = Situation("6 unknown: The Mark, 3 face-down, draw 3", kept=2, hidden=3, blind="The Mark")
MARK_ALL = Situation("8 unknown: The Mark, 3 face-down, draw 5", kept=0, hidden=3, blind="The Mark")


def clear_caches() -> None:
    """Empty every functools cache in the hands and engine modules."""
    for module in (calculatro.hands, calculatro.engine):
        for value in vars(module).values():
            if hasattr(value, "cache_clear"):
                value.cache_clear()


@dataclass(frozen=True)
class Scenario:
    situation: Situation
    targets: tuple[str, ...]
    jokers: str  # a JOKERS key
    deck: str = "standard"

    @property
    def target_label(self) -> str:
        return " + ".join(self.targets) + (" (any)" if len(self.targets) > 1 else "")

    @property
    def id(self) -> str:
        return f"{self.deck} | {self.situation.label} | {self.target_label} | {self.jokers}"

    def state(self) -> tuple[GameState, list[int]]:
        """The game state and the hand indices selected for discarding."""
        cards = {(c.rank_id, c.suit): c for c in deck(self.deck)}
        hand_cards = [cards.pop(_parse(t)) for t in HAND]
        if self.situation.blind == "The Mark":
            hand_cards.sort(key=lambda c: c.rank_id in (11, 12, 13))
        known = len(HAND) - self.situation.hidden
        hand = hand_cards[:known] + [Card(hidden=True)] * self.situation.hidden
        state = GameState(
            in_run=True, hand_size=len(HAND), hands_left=4, discards_left=3,
            hand=hand, deck=sorted(cards.values(), key=lambda c: (c.suit, c.rank_id)),
            hidden_pool=hand_cards[known:], jokers=[Joker(k) for k in JOKERS[self.jokers]],
            blind=self.situation.blind,
        )
        return state, list(range(self.situation.kept, known))

    def target(self) -> Target:
        return combine([get_target(n) for n in self.targets])

    def prepare(self) -> None:
        """Build and cache the target's patterns, a one-off cost per process."""
        if self.situation.cold:
            return
        state, _ = self.state()
        self.target().patterns(state.rules)

    def run(self, cancel: Event | None = None) -> Fraction:
        state, discard = self.state()
        if self.situation.cold:
            clear_caches()
        return evaluate_discard(state, self.target(), discard, cancel).probability


SINGLE_HANDS = ["Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House",
                "Four of a Kind", "Straight Flush", "Royal Flush", "Five of a Kind",
                "Flush House", "Flush Five"]
COMBINED = [("Full House", "Straight Flush"), ("Flush", "Straight Flush"),
            ("Two Pair", "Flush"), ("Straight", "Flush")]
# "Any target" rows timed with nothing cached: building them compares patterns
# across hands, which with Four Fingers and Shortcut are many.
COLD_COMBINED = [("Full House", "Straight Flush"), ("Straight Flush", "Royal Flush"),
                 ("Straight Flush", "Flush House"), tuple(SINGLE_HANDS)]


def _scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    for jokers in JOKERS:
        # Every hand, in the common case and in the largest one.
        for situation in (KEEP_3, THE_HOUSE):
            out += [Scenario(situation, (h,), jokers) for h in SINGLE_HANDS]
        # Straight Flush across the range of unknown cards.
        out += [Scenario(s, ("Straight Flush",), jokers) for s in (UNKNOWN_6, UNKNOWN_7)]
        # "Any target" rows.
        out += [Scenario(THE_HOUSE, c, jokers) for c in COMBINED]
        # Unseen Wild Cards count as every suit, which the engine handles differently.
        out += [Scenario(s, ("Straight Flush",), jokers, "wild") for s in (KEEP_3, UNKNOWN_6, UNKNOWN_7, THE_HOUSE)]
        out += [Scenario(THE_HOUSE, t, jokers, "wild")
                for t in (("Flush House",), ("Full House", "Straight Flush"), ("Pair", "Flush"))]
        out += [Scenario(COLD_KEEP_3, c, jokers) for c in COLD_COMBINED]
        # The Mark: one search per number of face cards among the unknown cards.
        out += [Scenario(s, t, jokers) for s in (MARK_KEEP_2, MARK_ALL)
                for t in (("Pair",), ("Flush",), ("Full House",), ("Straight Flush",), ("Full House", "Straight Flush"))]
    return out


SCENARIOS = _scenarios()
