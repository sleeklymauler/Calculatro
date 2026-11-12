"""Benchmark scenarios for the probability engine.

Each scenario is one odds row as `report.build_report` computes it: a game state, the
in-game selection to discard, and a target (several targets are combined into
the "Any target" row). States are built like the mod exports them, with
face-down cards hidden in hand and their identities among the `unseen`, so the
timings include everything `evaluate_discard` does.

"Unknown cards" are the cards the engine treats as drawn: kept face-down cards
plus the cards drawn after the discard.

"Nothing cached" scenarios time the first odds for a target, as when it's first
tracked or a joker changes the rules: every cache in `calculatro.hands` and
`calculatro.engine` is cleared before each run, so building the target's
patterns is timed too. Their situation keeps the search itself small.

"Best discard" scenarios time a click on a row's search button instead:
`search.best_discards`, which compares every discard of the hand. Their result
is the best odds and the options found.

Apart from those, only APIs that also exist in older versions are used here, so
benchmark.py can time an older checkout (see docs/performance/README.md).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from threading import Event

import calculatro.engine
import calculatro.hands
from calculatro.engine import evaluate_discard
from calculatro.hands import Target, combine, get_target
from calculatro.state import SUITS, Card, GameState, Joker


def _unseen(deck, hidden: list[Card]) -> dict[str, list[Card]]:
    """GameState's fields for the undrawn deck and the face-down cards' identities:
    one `unseen` list, or, in older versions, `deck` and `hidden_pool`."""
    deck = sorted(deck, key=lambda c: (c.suit, c.rank_id))
    if "unseen" in GameState.__dataclass_fields__:
        return {"unseen": deck + hidden}
    return {"deck": deck, "hidden_pool": hidden}


def _sorted(cards: list[Card], hand: list[Card], sorts: tuple[str, ...], gone: int = 0,
            earlier: tuple[tuple[str, tuple[str, ...]], ...] = (), texts: tuple[str, ...] = ()) -> dict:
    """GameState's fields for the hand's sorts in `sorts` (modes), as the mod
    exports them: `cards` are the hand's cards as they really are, and `hand`
    as the player sees them. Ids are numbered by first appearance, the rank
    sort first. Nothing in versions from before the sorts were used.

    The last `gone` cards, face down, were discarded face down after the sorts:
    they're in the discard pile, and in the sorts only in versions that keep them
    there (stage 3 of docs/plans/face-down-windows.md).

    `earlier` are sorts seen before those, as (mode, cards): each is one of the
    hand's, by its text in `texts`, or a standard deck's card since gone, "?"
    before it if it was discarded face down (in the discard pile, and unseen), and
    otherwise played or discarded face up. Versions that keep only the latest sort
    of each mode (before stage 4) get only `sorts`."""
    left = len(hand) - gone
    downs = list(dict.fromkeys(t for _, ts in earlier for t in ts if t.startswith("?")))
    hidden = gone + len(downs)
    discard = {"discard": [Card(hidden=True)] * hidden} if hidden else {}
    if not sorts or "observations" not in GameState.__dataclass_fields__:
        return {"hand": hand[:left], **discard}
    from calculatro.state import SCHEMA, Observation, ObservedCard
    suit ={"Diamonds": 0, "Clubs": 1, "Hearts": 2, "Spades": 3}

    def true(i: int | str) -> Card:
        return cards[i] if isinstance(i, int) else _card(*_parse(i.lstrip("?")))

    def key(mode: str):
        if mode == "desc":
            return lambda i: (true(i).rank_id, suit[true(i).suit])
        return lambda i: (suit[true(i).suit], true(i).rank_id)

    keeps_gone = "discarded" in ObservedCard.__dataclass_fields__
    keeps_earlier = SCHEMA >= 14
    shown = [i for i in range(len(cards)) if i < left or keeps_gone]
    orders: list[tuple[str, list[int | str]]] = []
    if keeps_earlier:
        orders += [(mode, sorted([texts.index(t) if t in texts else t for t in ts], key=key(mode), reverse=True))
                   for mode, ts in earlier]
    orders += [(mode, sorted(shown, key=key(mode), reverse=True)) for mode in sorted(sorts)]
    ids = list(dict.fromkeys(i for _, order in orders for i in order))

    def observed_card(i: int | str) -> ObservedCard:
        if isinstance(i, str):
            return ObservedCard(discarded=True) if i.startswith("?") else ObservedCard(rank_id=true(i).rank_id,
                                                                                       suit=true(i).suit)
        if i >= left:
            return ObservedCard(discarded=True)
        return ObservedCard(hand=i) if hand[i].hidden else ObservedCard(rank_id=cards[i].rank_id, suit=cards[i].suit)
    return {"hand": [replace(c, latent=ids.index(i)) if c.hidden else c for i, c in enumerate(hand[:left])],
            "observations": tuple(Observation(m, tuple(ids.index(i) for i in o)) for m, o in orders),
            "observed": tuple(observed_card(i) for i in ids), **discard}


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
    # With "The Mark", the face-down cards are the hand's face cards; with
    # "Cerulean Bell", the first card discarded is the forced one.
    blind: str | None = None
    discards: int = 3  # with none, the selection is played
    jokers: tuple[Joker, ...] = ()  # besides the scenario's
    sorts: tuple[str, ...] = ()  # the modes the hand was seen sorted in, with its cards in place (_sorted)
    gone: int = 0  # of the face-down cards, those discarded face down after the sorts (_sorted)
    earlier: tuple[tuple[str, tuple[str, ...]], ...] = ()  # sorts seen before those, as (mode, cards) (_sorted)
    discard: tuple[int, ...] | None = None  # the hand indices to discard, if not the known cards not kept
    hand: tuple[str, ...] = tuple(HAND)

    @property
    def unknown(self) -> int:
        return self.hidden - self.gone + (len(self.hand) - self.hidden - self.kept)


KEEP_3 = Situation("Keep 3, draw 5", kept=3, hidden=0)
UNKNOWN_6 = Situation("6 unknown: 1 face-down, draw 5", kept=2, hidden=1)
UNKNOWN_7 = Situation("7 unknown: 2 face-down, draw 5", kept=1, hidden=2)
THE_HOUSE = Situation("8 unknown: The House (all face-down)", kept=0, hidden=8)
COLD_KEEP_3 = Situation("Keep 3, draw 5, nothing cached", kept=3, hidden=0, cold=True)
# The Mark draws face cards face down, so they're HAND's 3 face cards, known to be face cards.
MARK_KEEP_2 = Situation("6 unknown: The Mark, 3 face-down, draw 3", kept=2, hidden=3, blind="The Mark")
MARK_ALL = Situation("8 unknown: The Mark, 3 face-down, draw 5", kept=0, hidden=3, blind="The Mark")
# The hand seen sorted, so the odds average over what its face-down cards can be, between their
# neighbours in each sort. In HAND sorted by rank, J♥ is between Q♣ and 9♠, and 5♦ between 7♥ and 3♠.
SORTED_1 = Situation("6 unknown: 1 face-down, draw 5, sorted by rank", kept=2, hidden=1, sorts=("desc",))
SORTED_2 = Situation("7 unknown: 2 face-down, draw 5, sorted by rank", kept=1, hidden=2, sorts=("desc",))
SORTED_2_SUIT = Situation("7 unknown: 2 face-down, draw 5, sorted by suit", kept=1, hidden=2, sorts=("suit desc",))
SORTED_2_BOTH = Situation("7 unknown: 2 face-down, draw 5, sorted both ways", kept=1, hidden=2,
                          sorts=("desc", "suit desc"))
# The House's first hand, kept: a sort of face-down cards only says nothing about what they are.
THE_HOUSE_SORTED = Situation("8 unknown: The House (all face-down), sorted by rank", kept=0, hidden=8,
                             sorts=("desc",))
# Sorted by suit too, the two sorts together say how the cards split into suits: no shortcut, and no face-up
# card to narrow down the deals (posterior.summed). In three suits, the suit sort's runs are 3, 2 and 3 (K Q 5,
# J 7, 9 8 3), as in a hand seen in game whose odds took 6-17 s each. (HAND's four suits, in runs of 2, 3, 1
# and 2, leave far fewer deals: under a second.)
THE_HOUSE_BOTH = Situation("8 unknown: The House (all face-down), sorted both ways", kept=0, hidden=8,
                           sorts=("desc", "suit desc"), hand=("KS", "QS", "JH", "9C", "8C", "7H", "5S", "3C"))
# After The House's first discard, the face-down cards kept have face-up neighbours: a long run.
HOUSE_AFTER = Situation("5 unknown: 5 face-down kept, sorted by rank", kept=3, hidden=5, sorts=("desc",))
# Then one of the face-down cards discarded face down: still in the sorts, where it bounds the other.
SORTED_2_GONE = Situation("6 unknown: 2 face-down, 1 since discarded, draw 5, sorted by rank", kept=1, hidden=2,
                          sorts=("desc",), gone=1)
SORTED_2_BOTH_GONE = Situation("6 unknown: 2 face-down, 1 since discarded, draw 5, sorted both ways", kept=1,
                               hidden=2, sorts=("desc", "suit desc"), gone=1)
# Earlier sorts (stage 4). The House's first hand sorted by rank, all face down; then 3 of them
# discarded face down, and 3 drawn face up, re-sorted by rank. That first sort still holds all 8.
HOUSE_EARLIER = Situation("5 unknown: The House after its first discard, sorted by rank before and after", kept=3,
                          hidden=5, sorts=("desc",),
                          earlier=(("desc", ("?AS", "?6C", "?TD", "QC", "KD", "3S", "5D", "JH")),))
# The Fish: the hand before had 5 cards drawn face down after a play; 3 of them were played (turned
# up), the K and the 5 kept, and 3 more drawn face down. The earlier sort has the K between the A and
# the 10 played, and the 5 between the 7 and the 4.
FISH_EARLIER = Situation("5 unknown: The Fish, 2 face-down kept from the hand before, sorted by rank both times",
                         kept=3, hidden=5, sorts=("desc",),
                         earlier=(("desc", ("7H", "8H", "9S", "AS", "KD", "TC", "5D", "4D")),))
# The House's first hand split: sorted by rank it's K Q J 9 8 7 5 3, hand indices 4 3 7 2 1 0 6 5. Throwing
# away the 3 highest leaves the 5 lowest kept; the 3 in the middle, the 3 above and the 2 below.
HOUSE_SPLIT_TOP = Situation("8 unknown: The House (all face-down), sorted by rank, the 3 highest discarded", kept=0,
                            hidden=8, sorts=("desc",), discard=(4, 3, 7))
HOUSE_SPLIT_MIDDLE = Situation("8 unknown: The House (all face-down), sorted by rank, the 3 in the middle discarded",
                               kept=0, hidden=8, sorts=("desc",), discard=(2, 1, 0))
MARK_SORTED = Situation("6 unknown: The Mark, 3 face-down, draw 3, sorted by rank", kept=2, hidden=3,
                        blind="The Mark", sorts=("desc",))
# Face-down cards in the sorts under The Hook (the selection played, then 2 kept cards thrown away at random)
# and Cerulean Bell (the first card discarded forced, or with HOUSE_AFTER's, a face-down one): neither takes
# windows (engine._windows_for), so their deals are summed. Measured for face-down stage 5b; with 5 face-down
# cards kept, the other targets take from 20 s to over a minute, so they're left out.
SORTED_2_HOOK = Situation("7 unknown: 2 face-down, draw 5, sorted by rank, The Hook, played", kept=1, hidden=2,
                          sorts=("desc",), blind="The Hook", discards=0)
SORTED_2_BELL = Situation("7 unknown: 2 face-down, draw 5, sorted by rank, Cerulean Bell", kept=1, hidden=2,
                          sorts=("desc",), blind="Cerulean Bell")
HOUSE_AFTER_HOOK = Situation("5 unknown: 5 face-down kept, sorted by rank, The Hook, 1 played", kept=3, hidden=5,
                             sorts=("desc",), blind="The Hook", discards=0, discard=(0,))
HOUSE_AFTER_BELL = Situation("5 unknown: 5 face-down kept, sorted by rank, Cerulean Bell, 1 discarded", kept=3,
                             hidden=5, sorts=("desc",), blind="Cerulean Bell", discard=(0,))
# Cerulean Bell forces a random card of the hand after the draw: one search per kept card and per
# kind of unseen card.
BELL_KEEP_3 = Situation("Keep 3, draw 5, Cerulean Bell", kept=3, hidden=0, blind="Cerulean Bell")
BELL_KEEP_6 = Situation("Keep 6, draw 2, Cerulean Bell", kept=6, hidden=0, blind="Cerulean Bell")
# Crimson Heart, with the selection played: after the draw it lifts the debuffed joker's debuff and debuffs
# another, so the odds average over the rules that leaves. With the scenario's Shortcut, the Four Fingers
# here comes back, and either the Joker is debuffed (Four Fingers + Shortcut) or Shortcut is (Four Fingers).
CRIMSON_KEEP_3 = Situation("Keep 3, draw 5, Crimson Heart, played", kept=3, hidden=0, blind="Crimson Heart",
                           discards=0, jokers=(Joker("j_four_fingers", debuff=True), Joker("j_joker")))


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
        hand_cards = [cards.pop(_parse(t)) for t in self.situation.hand]
        for _, texts in self.situation.earlier:  # played or discarded face up since: not unseen
            for t in texts:
                if t not in self.situation.hand and not t.startswith("?"):
                    cards.pop(_parse(t), None)
        if self.situation.blind == "The Mark":
            hand_cards.sort(key=lambda c: c.rank_id in (11, 12, 13))
        known = len(hand_cards) - self.situation.hidden
        hand = hand_cards[:known] + [Card(hidden=True)] * self.situation.hidden
        if self.situation.blind == "Cerulean Bell" and "forced" in Card.__dataclass_fields__:
            hand[self.situation.kept] = replace(hand[self.situation.kept], forced=True)
        state = GameState(
            in_run=True, hand_size=len(hand_cards) - self.situation.gone, hands_left=4,
            discards_left=self.situation.discards,
            **_sorted(hand_cards, hand, self.situation.sorts, self.situation.gone, self.situation.earlier,
                      self.situation.hand),
            **_unseen(cards.values(), hand_cards[known:]),
            jokers=[Joker(k) for k in JOKERS[self.jokers]] + list(self.situation.jokers), blind=self.situation.blind,
        )
        if self.situation.discard is not None:
            return state, list(self.situation.discard)
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

    def estimate(self, seconds: float):
        """The estimate the panel shows while the exact odds are slow (engine.Estimate)."""
        from calculatro.engine import estimate_discard
        state, discard = self.state()
        return estimate_discard(state, self.target(), discard, seconds=seconds)


@dataclass(frozen=True)
class Hand:
    """A hand to find the best discard for, with the rest of the deck unseen."""
    label: str
    cards: tuple[str, ...]
    hidden: int = 0  # the last `hidden` cards are face down
    blind: str | None = None  # with "Cerulean Bell", the first card is the forced one
    discards: int = 3
    jokers: tuple[Joker, ...] = ()  # besides the scenario's
    dna_copies: int = 0
    sorts: tuple[str, ...] = ()  # as Situation.sorts


@dataclass(frozen=True)
class BestDiscard:
    """A best-discard search: the odds row's target, for `hand`."""
    hand: Hand
    targets: tuple[str, ...]
    jokers: str  # a JOKERS key
    deck: str = "standard"

    @property
    def situation(self) -> Hand:  # benchmark.py's tables group by situation label
        return self.hand

    target_label = Scenario.target_label

    @property
    def id(self) -> str:
        return f"{self.deck} | {self.hand.label} | {self.target_label} | {self.jokers}"

    def state(self) -> GameState:
        cards = {(c.rank_id, c.suit): c for c in deck(self.deck)}
        hand_cards = [cards.pop(_parse(t)) for t in self.hand.cards]
        known = len(hand_cards) - self.hand.hidden
        hand = hand_cards[:known] + [Card(hidden=True)] * self.hand.hidden
        if self.hand.blind == "Cerulean Bell":
            hand[0] = replace(hand[0], forced=True)
        return GameState(
            in_run=True, hand_size=len(hand), hands_left=4, discards_left=self.hand.discards,
            **_sorted(hand_cards, hand, self.hand.sorts), **_unseen(cards.values(), hand_cards[known:]),
            jokers=[Joker(k) for k in JOKERS[self.jokers]] + list(self.hand.jokers), blind=self.hand.blind,
            **({"dna_copies": self.hand.dna_copies} if self.hand.dna_copies else {}),
        )

    target = Scenario.target

    def prepare(self) -> None:
        self.target().patterns(self.state().rules)

    def run(self, cancel: Event | None = None) -> str:
        from calculatro.search import best_discards
        found = best_discards(self.state(), self.target(), cancel)
        return f"{found.probability} ({len(found.options)} options, first {found.options[0]})"

    def estimate(self, seconds: float):
        """The search given up as soon as it has its choices, answering from estimates in about `seconds`
        (search.estimated_best), the seconds that took, and the exact odds of the discard it picks first."""
        import time

        from calculatro.engine import Cancelled
        from calculatro.search import Progress, best_discards, estimated_best
        state, target = self.state(), self.target()
        progress = Progress()

        class GiveUp:  # set once it has its choices
            def is_set(self) -> bool:
                return progress.estimate is not None
        start = time.perf_counter()
        try:
            best_discards(state, target, GiveUp(), progress)
        except Cancelled:
            pass
        found = estimated_best(progress, None, seconds)
        elapsed = time.perf_counter() - start
        return found, elapsed, evaluate_discard(state, target, found.options[0]).probability


SINGLE_HANDS = ["Pair", "Two Pair", "Three of a Kind", "Straight", "Flush", "Full House",
                "Four of a Kind", "Straight Flush", "Five of a Kind",
                "Flush House", "Flush Five"]
COMBINED = [("Full House", "Straight Flush"), ("Flush", "Straight Flush"),
            ("Two Pair", "Flush"), ("Straight", "Flush")]
# "Any target" rows timed with nothing cached: building them compares patterns
# across hands, which with Four Fingers and Shortcut are many.
COLD_COMBINED = [("Full House", "Straight Flush"), ("Straight Flush", "Flush House"), tuple(SINGLE_HANDS)]


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
        out += [Scenario(s, t, jokers) for s in (BELL_KEEP_3, BELL_KEEP_6)
                for t in (("Straight",), ("Flush",), ("Full House",), ("Straight Flush",),
                          ("Full House", "Straight Flush"))]
        out += [Scenario(s, t, jokers) for s in (MARK_KEEP_2, MARK_ALL)
                for t in (("Pair",), ("Flush",), ("Full House",), ("Straight Flush",),
                          ("Full House", "Straight Flush"))]
    # The hand's sorts: one evaluation per kind of card the face-down ones can be.
    for jokers in ("No jokers", "Four Fingers + Shortcut"):
        out += [Scenario(s, t, jokers) for s in (SORTED_1, SORTED_2, SORTED_2_SUIT, SORTED_2_BOTH)
                for t in (("Pair",), ("Flush",), ("Full House",), ("Straight Flush",),
                          ("Full House", "Straight Flush"))]
        # About 260,000 deals, which Straight Flush tells almost all apart: two windows (posterior.windows).
        out += [Scenario(HOUSE_AFTER, t, jokers) for t in (("Pair",), ("Flush",), ("Full House",), ("Straight Flush",))]
        # Earlier sorts kept (stage 4). HOUSE_EARLIER's first sort, of 8 face-down cards, puts the 3 since
        # discarded in wide windows: about 1.7 * 10^8 deals, summed as they're dealt (posterior.summed).
        out += [Scenario(s, t, jokers) for s in (FISH_EARLIER, HOUSE_EARLIER)
                for t in (("Pair",), ("Flush",), ("Full House",))]
        # A discard that splits The House's first hand: split again by the cards at the edges (engine._split).
        # Straight Flush with Four Fingers + Shortcut takes about 2 minutes there, so it's left out.
        split_top = [("Pair",), ("Flush",), ("Full House",)] + ([("Straight Flush",)] if jokers == "No jokers" else [])
        out += [Scenario(HOUSE_SPLIT_TOP, t, jokers) for t in split_top]
        out += [Scenario(HOUSE_SPLIT_MIDDLE, t, jokers) for t in (("Pair",), ("Flush",))]
        out += [Scenario(s, t, jokers) for s in (SORTED_2_GONE, SORTED_2_BOTH_GONE)
                for t in (("Pair",), ("Flush",), ("Straight",), ("Full House",), ("Full House", "Straight Flush"))]
        out += [Scenario(s, t, jokers) for s in (THE_HOUSE_SORTED, MARK_SORTED)
                for t in (("Flush",), ("Straight Flush",), ("Full House", "Straight Flush"))]
    out += [Scenario(THE_HOUSE_BOTH, t, "No jokers") for t in (("Pair",), ("Straight Flush",))]
    out += [Scenario(s, t, "No jokers") for s in (SORTED_2_HOOK, SORTED_2_BELL)
            for t in (("Pair",), ("Full House",), ("Straight Flush",))]
    out += [Scenario(HOUSE_AFTER_HOOK, t, "No jokers") for t in (("Pair",), ("Flush",))]
    out += [Scenario(HOUSE_AFTER_BELL, ("Pair",), "No jokers")]
    # Crimson Heart: Straight and Straight Flush see both rule sets, Flush only whether Four Fingers is on (one
    # search), and Pair and Full House neither.
    out += [Scenario(CRIMSON_KEEP_3, t, "Shortcut")
            for t in (("Pair",), ("Straight",), ("Flush",), ("Full House",), ("Straight Flush",),
                      ("Full House", "Straight Flush"))]
    return out


HAND_10 = (*HAND, "2C", "TD")  # a bigger hand size, from jokers or vouchers
SEARCH_HANDS = [
    Hand("Best discard: 8 cards", tuple(HAND)),
    Hand("Best discard: 8 cards, two pairs", ("7H", "7S", "9S", "QC", "QD", "3S", "5D", "JH")),
    Hand("Best discard: 10 cards", HAND_10),
]
# Bosses that change what a discard does, on the 8-card hand.
SEARCH_BOSSES = [
    Hand("Best discard: 8 cards, 2 face-down", tuple(HAND), hidden=2, blind="The House"),
    Hand("Best discard: 8 cards, Cerulean Bell", tuple(HAND), blind="Cerulean Bell"),
    Hand("Best discard: 8 cards, The Hook, played", tuple(HAND), blind="The Hook", discards=0),
    # DNA with a Blueprint copying it: a lone card played comes back twice.
    Hand("Best discard: 8 cards, DNA + Blueprint, played", tuple(HAND), discards=0, dna_copies=2),
]
# The hand seen sorted (Situation.sorts): every discard averages over what the face-down cards can be.
SEARCH_SORTED = [
    Hand("Best discard: 8 cards, 2 face-down, sorted by rank", tuple(HAND), hidden=2, sorts=("desc",)),
    Hand("Best discard: 8 cards, 2 face-down, sorted both ways", tuple(HAND), hidden=2, sorts=("desc", "suit desc")),
]
# Crimson Heart, with the selection played: see CRIMSON_KEEP_3. With the scenario's jokers, the debuffed
# joker below comes back and one of the others is debuffed.
SEARCH_CRIMSON = [
    # Four Fingers + Shortcut, then either Four Fingers or Shortcut alone.
    (Hand("Best discard: 8 cards, Crimson Heart, played, Joker debuffed", tuple(HAND), blind="Crimson Heart",
          discards=0, jokers=(Joker("j_joker", debuff=True),)), "Four Fingers + Shortcut"),
    # Shortcut, then Four Fingers with or without Shortcut: the slow Straight Flush.
    (Hand("Best discard: 8 cards, Crimson Heart, played, Four Fingers debuffed", tuple(HAND), blind="Crimson Heart",
          discards=0, jokers=(Joker("j_four_fingers", debuff=True), Joker("j_joker"))), "Shortcut"),
]
# Hand-size jokers reach 11 cards. "Any target" then has about 1000 choices, and the sum of the hands' odds
# is at least 1 for hundreds of them: the first hand holds a Full House, so the best odds are 1, and the
# second doesn't, so they're below 1.
SEARCH_LARGE = [
    Hand("Best discard: 11 cards, holding a Full House",
         ("QD", "JS", "KC", "JH", "4S", "AC", "8H", "KS", "JC", "7S", "5C")),
    Hand("Best discard: 11 cards, holding no target",
         ("4C", "AS", "8D", "4D", "AC", "9S", "7S", "QC", "3S", "QD", "9C")),
]
SEARCH_TARGETS = [("Pair",), ("Two Pair",), ("Straight",), ("Flush",), ("Full House",), ("Straight Flush",),
                  ("Flush", "Straight", "Full House")]
SEARCH_JOKERS = ["No jokers", "Four Fingers + Shortcut", "Smeared"]


def _search_scenarios() -> list[BestDiscard]:
    out = [BestDiscard(h, t, j) for j in SEARCH_JOKERS for h in SEARCH_HANDS for t in SEARCH_TARGETS]
    out += [BestDiscard(h, t, "No jokers") for h in SEARCH_BOSSES
            for t in (("Flush",), ("Full House",), ("Straight Flush",), ("Flush", "Straight", "Full House"))]
    out += [BestDiscard(h, t, "No jokers") for h in SEARCH_SORTED
            for t in (("Flush",), ("Full House",), ("Straight Flush",), ("Flush", "Straight", "Full House"))]
    out += [BestDiscard(h, t, "Four Fingers + Shortcut") for h in SEARCH_SORTED
            for t in (("Flush",), ("Full House",), ("Straight Flush",), ("Flush", "Straight", "Full House"))]
    out += [BestDiscard(h, ("Flush", "Straight", "Full House"), "No jokers") for h in SEARCH_LARGE]
    out += [BestDiscard(h, t, j) for h, j in SEARCH_CRIMSON
            for t in (("Straight",), ("Flush",), ("Full House",), ("Straight Flush",),
                      ("Flush", "Straight", "Full House"))]
    return out


SCENARIOS: list[Scenario | BestDiscard] = _scenarios() + _search_scenarios()
