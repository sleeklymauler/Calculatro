"""Rounds played out from a shuffled deck, to check the posterior against: the first hand drawn every way,
sorted, cards thrown away, drawn every way and sorted again, keeping the deals whose every step looks as the player
saw it (`Round.worlds`). Used by test_posterior, and by test_hint, which replays them in the mocked game."""

import itertools
import random
from dataclasses import dataclass
from fractions import Fraction
from math import factorial, prod

from calculatro.state import SUITS, Card
from game_order import nominal
from poker import card
from state_oracle import STONE, Case, Worlds, printed


@dataclass
class Played:
    """A round played out, by card slot ("s0", "s1", ... in the order drawn)."""
    events: list[tuple]  # ("deal"/"sort"/"draw", mode, hand), or ("throw", "discard"/"play", thrown, hand)
    trace: list[tuple]  # what the player saw of each event
    what: dict[str, Card]  # what each slot drawn is
    down: set[str]  # the slots drawn face down and never turned up
    hand: list[str]
    sorts: list[tuple[str, list[str]]]  # every sort seen, in order


@dataclass
class Round:
    """A round from a shuffled `deck` (names to cards). The first hand is drawn with
    the cards of `first` face down or not, and sorted in `mode`. Each step is
    ("sort", mode), or ("discard" or "play", hand positions thrown away, for each
    card then drawn whether it's face down), which draws and re-sorts in the
    current mode as the game does. Played cards are turned up."""
    deck: dict[str, Card]
    first: list[bool]
    steps: list[tuple]
    mode: str = "desc"

    @property
    def facings(self) -> list[bool]:
        return self.first + [d for step in self.steps if step[0] != "sort" for d in step[2]]

    def play(self, drawn: list[str], tiebreak: dict[str, int]) -> Played:
        """The round with the deck's cards `drawn` in this order, and `tiebreak` for
        each slot (the game's hidden order of cards alike in print)."""
        what = {f"s{i}": self.deck[n] for i, n in enumerate(drawn)}
        face_down = dict(zip(what, self.facings))
        hand = list(what)[:len(self.first)]
        mode, turned = self.mode, set()
        events, trace, sorts = [], [], []

        def sort(kind: str) -> None:
            hand.sort(key=lambda s: nominal(printed(what[s]), mode, tiebreak[s]), reverse=True)
            events.append((kind, mode, list(hand)))
            trace.append((kind, mode, tuple((s, None if face_down[s] else printed(what[s])) for s in hand)))
            sorts.append((mode, list(hand)))

        sort("deal")
        count = len(self.first)
        for step in self.steps:
            if step[0] == "sort":
                mode = step[1]
                sort("sort")
                continue
            kind, positions, facings = step
            thrown = [hand[p] for p in positions]
            for s in thrown:
                hand.remove(s)
            if kind == "play":
                turned.update(s for s in thrown if face_down[s])
            events.append(("throw", kind, thrown, list(hand)))
            trace.append(("throw", kind, tuple((s, printed(what[s]) if kind == "play" or not face_down[s] else None)
                                               for s in thrown)))
            hand += [f"s{i}" for i in range(count, count + len(facings))]
            count += len(facings)
            sort("draw")
        down = {s for s in what if face_down[s] and s not in turned}
        return Played(events, trace, what, down, hand, sorts)

    def case(self, played: Played, **kw) -> Case:
        """The round's end as the mod would send it with every sort it saw kept."""
        left = list(self.deck)
        for c in played.what.values():
            left.remove(next(n for n in left if self.deck[n] == c))
        return Case(hand=played.hand, cards=played.what, down=played.down, views=played.sorts,
                    deck=[self.deck[n] for n in left], kw=kw)

    def worlds(self, truth: Played) -> Worlds:
        """Every order the deck can have been drawn in, with every hidden order of
        the cards alike in print, that the player would have seen as `truth`: what
        each slot is, the deck left, and its weight."""
        names = list(self.deck)
        facings = self.facings
        turned = {s for event in truth.events if event[0] == "throw" and event[1] == "play" for s in event[2]}
        # A card seen face up, or played, is one printed as it was.
        shown = [None if facings[i] and f"s{i}" not in turned else printed(truth.what[f"s{i}"])
                 for i in range(len(facings))]
        out = []

        def deal(drawn: list[str]) -> None:
            if len(drawn) == len(facings):
                cards = [self.deck[n] for n in drawn]
                groups: dict[object, list[str]] = {}
                for i, c in enumerate(cards):
                    groups.setdefault(printed(c), []).append(f"s{i}")
                hits = 0
                for orders in itertools.product(*(itertools.permutations(g) for g in groups.values())):
                    tiebreak = {s: i for order in orders for i, s in enumerate(order)}
                    hits += self.play(drawn, tiebreak).trace == truth.trace
                if hits:
                    what = {f"s{i}": c for i, c in enumerate(cards)}
                    out.append((what, [self.deck[n] for n in names if n not in drawn],
                                Fraction(hits, prod(factorial(len(g)) for g in groups.values()))))
                return
            for n in names:
                if n not in drawn and (shown[len(drawn)] is None or printed(self.deck[n]) == shown[len(drawn)]):
                    deal([*drawn, n])

        deal([])
        return out


def played(rnd: Round, drawn: list[str], seed: int = 0) -> Played:
    """The round with the deck drawn as `drawn`, and a random hidden order of the cards."""
    order = random.Random(seed).sample(range(100), len(drawn))
    return rnd.play(drawn, {f"s{i}": order[i] for i in range(len(drawn))})


def random_round(rng: random.Random, copies=False, stones=False, plays=False, hands=2) -> tuple[Round, list[str]]:
    """A narrow deck, a first hand of 4 or 5 with 1 or 2 face down, sorted, then
    `hands` times: maybe a sort in the other mode, then 1 or 2 cards thrown away
    (played, with `plays`, every other time), and as many drawn, one of them maybe
    face down (as The Wheel or The Fish do). Then maybe a last sort."""
    suits = rng.sample(SUITS, 2)
    cards = [card(r, s) for r in range(6, 12) for s in suits]
    rng.shuffle(cards)
    cards = cards[:rng.randint(9, 10)]
    if copies:
        cards += [cards[0]]
    if stones:
        cards += [STONE, STONE]
    deck = {f"c{i}": c for i, c in enumerate(cards)}
    size = rng.randint(4, 5)
    first = [False] * size
    for i in rng.sample(range(size), rng.randint(1, 2)):
        first[i] = True
    mode = start = rng.choice(["desc", "suit desc"])
    steps: list[tuple] = []
    downs = sum(first)
    for k in range(hands):
        if rng.random() < 0.5:
            mode = "suit desc" if mode == "desc" else "desc"
            steps.append(("sort", mode))
        thrown = sorted(rng.sample(range(size), rng.randint(1, 2)))
        facings = [False] * len(thrown)
        if downs < 4 and rng.random() < 0.6:
            facings[rng.randrange(len(thrown))] = True
            downs += 1
        steps.append(("play" if plays and k % 2 else "discard", thrown, facings))
    if rng.random() < 0.5:
        steps.append(("sort", rng.choice(["desc", "suit desc"])))
    rnd = Round(deck, first, steps, start)
    return rnd, rng.sample(list(deck), len(rnd.facings))


def round_discards(rng: random.Random, case: Case) -> list[list[int]]:
    n = len(case.hand)
    latent = [i for i, name in enumerate(case.hand) if name in case.down] or [0]
    picks = [[], [rng.choice(latent)], sorted(rng.sample(range(n), 2))]
    return [list(d) for d in dict.fromkeys(tuple(d) for d in picks)]
