"""The odds with what the hand's sorts show of its face-down cards, checked
against dealing every way (docs/plans/face-down-windows.md#tests).

The oracle doesn't use the posterior's model: it deals the unseen cards to the
face-down ones every way, gives the cards alike in print every order of the
game's hidden tie-breakers (the same in both sorts), sorts with the game's
get_nominal (`game_order`), keeps the deals that show what the player saw, and
then plays out every draw.
"""

from __future__ import annotations

import itertools
import random
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from math import factorial, prod

import pytest

from calculatro import engine
from calculatro.cards import CardQuery, parse_query
from calculatro.engine import Cancelled, evaluate_cards, evaluate_discard, flipped_by_mark, posterior
from calculatro.hands import Target, combine, get_target
from calculatro.posterior import reference
from calculatro.search import best_card_discards, best_discards
from calculatro.state import SCHEMA, SUITS, Card, GameState
from game_order import nominal
from test_engine import card, contains, contains_forced
from test_search import check as check_search

STONE = Card(enhancement="m_stone")


def as_json(c: Card) -> dict:
    return {"rank": c.rank, "rank_id": c.rank_id, "suit": c.suit, "enhancement": c.enhancement,
            "edition": c.edition, "seal": c.seal, "debuff": c.debuff}


@dataclass
class Case:
    """A hand whose sorts the player saw, and what its face-down cards really are.

    `hand` names its cards in order, `cards` says what every named card is, and
    `down` are the face-down ones: those not in `hand` were discarded face down
    after the sorts. `views` are the sorts seen, by name: they may hold face-up
    cards no longer in hand, and miss face-down ones (no evidence). `deck` is the
    rest of the unseen cards, `discarded` the cards discarded face down that no
    sort has, and `kw` the snapshot's other fields.
    """
    hand: list[str]
    cards: dict[str, Card]
    down: set[str]
    views: dict[str, list[str]]
    deck: list[Card]
    discarded: list[Card] = field(default_factory=list)
    forced: str | None = None
    kw: dict = field(default_factory=dict)

    @property
    def gone(self) -> list[str]:
        """The face-down cards discarded after the sorts, in the order the snapshot gives them ids."""
        return [n for n in self.first_seen() if n in self.down and n not in self.hand]

    def first_seen(self) -> list[str]:
        return list(dict.fromkeys(n for mode in ("desc", "suit desc") for n in self.views.get(mode, ())))

    @property
    def unseen(self) -> list[Card]:
        return [*self.deck, *(self.cards[n] for n in self.hand if n in self.down),
                *(self.cards[n] for n in self.gone), *self.discarded]

    def state(self) -> GameState:
        """As the mod would export it, parsed."""
        ids: dict[str, int] = {}
        for view in self.views.values():
            for name in view:
                ids.setdefault(name, len(ids))

        def entry(name: str) -> dict:
            if name in self.down:
                if name not in self.hand:
                    return {"id": ids[name], "discarded": True}
                return {"id": ids[name], "hand": self.hand.index(name)}
            c = self.cards[name]
            return {"id": ids[name], "stone": True} if c.is_stone else {"id": ids[name], "rank_id": c.rank_id,
                                                                        "suit": c.suit}

        def in_hand(name: str) -> dict:
            out = {"hidden": True} if name in self.down else as_json(self.cards[name])
            return {**out, "forced": True} if name == self.forced else out

        return GameState.from_json({
            "schema": SCHEMA, "in_run": True, "hand_size": len(self.hand), "hands_left": 3, "discards_left": 1,
            "hand": [in_hand(n) for n in self.hand], "unseen": [as_json(c) for c in self.unseen],
            "discard": [{"hidden": True}] * (len(self.discarded) + len(self.gone)),
            "observations": [{"mode": m, "cards": [entry(n) for n in v]} for m, v in self.views.items()],
            **self.kw})


def printed(c: Card):
    return "Stone" if c.is_stone else (c.rank_id, c.suit)


def chance_seen(views: dict[str, list[str]], what: dict[str, Card]) -> Fraction:
    """The chance that the game sorts these cards as `views`: cards alike in print
    are in a random order, which is the same in both sorts."""
    groups: dict[object, list[str]] = {}
    for name, c in what.items():
        groups.setdefault(printed(c), []).append(name)
    hits = 0
    for orders in itertools.product(*(itertools.permutations(g) for g in groups.values())):
        tiebreak = {name: i for order in orders for i, name in enumerate(order)}
        hits += all(sorted(view, key=lambda n: nominal(printed(what[n]), mode, tiebreak[n]), reverse=True) == view
                    for mode, view in views.items())
    return Fraction(hits, prod(factorial(len(g)) for g in groups.values()))


Holds = Callable[[list[Card], bool], Fraction]  # a final hand, whether its first card is forced (Cerulean Bell)


def worlds(case: Case) -> list[tuple[dict[str, Card], list[Card], Fraction]]:
    """Every deal of the unseen cards to the face-down ones the player's view
    allows: what each named card is, the deck left, and the deal's weight."""
    state = case.state()
    unseen = case.unseen
    assert not case.down - set(case.hand) - set(case.gone), "a card discarded face down in no sort"
    slots = ([n for n in case.hand if n in case.down] + case.gone
             + [f"discarded {i}" for i in range(len(case.discarded))])
    allowed = [j for j, c in enumerate(unseen) if not state.mark or flipped_by_mark(c, state.rules)]
    shown = list(dict.fromkeys(n for view in case.views.values() for n in view))
    out = []
    for picked in itertools.permutations(allowed, len(slots)):
        what = {**case.cards, **{n: unseen[j] for n, j in zip(slots, picked)}}
        weight = chance_seen(case.views, {n: what[n] for n in shown})
        if weight:
            out.append((what, [c for j, c in enumerate(unseen) if j not in picked], weight))
    return out


def oracle(case: Case, discard: list[int], holds: Holds) -> Fraction:
    """The odds after discarding `discard`, by dealing every way and drawing every way."""
    state = case.state()
    thrown = {case.hand[i] for i in discard}
    kept = [n for n in case.hand if n not in thrown]
    copies = state.dna_copies if (state.discards_left <= 0 and not state.hands_played and len(discard) == 1) else 0
    hooked = min(2, len(kept)) if state.hook and discard and state.discards_left <= 0 else 0
    total = norm = Fraction(0)
    for what, deck, weight in worlds(case):
        if not discard:
            draws = 0
        elif state.serpent:
            draws = 3
        else:
            draws = state.hand_size - len(kept) + hooked - copies
        draws = max(0, min(draws, len(deck)))
        added = [what[case.hand[discard[0]]]] * copies if copies else []
        outcomes = []
        for left in itertools.combinations(kept, len(kept) - hooked):
            # Keeping the hand keeps Cerulean Bell's forced card; after a draw it's random.
            first = [case.forced] if case.forced in left and not discard else []
            rest = [n for n in left if n not in first]
            for drawn in itertools.combinations(deck, draws):
                outcomes.append(holds([what[n] for n in first + rest] + added + list(drawn), bool(first)))
        total += weight * sum(outcomes) / len(outcomes)
        norm += weight
    return total / norm


def target_holds(target: Target, state: GameState) -> Holds:
    names, rules = set(target.hands), state.rules
    memo: dict[tuple, Fraction] = {}

    def holds(final: list[Card], forced_first: bool) -> Fraction:
        key = (forced_first, final[0] if forced_first else None,
               frozenset(Counter(final[forced_first:]).items()))
        if key not in memo:
            if forced_first:
                memo[key] = Fraction(contains_forced(final[1:], final[0], names, rules))
            elif state.bell:
                memo[key] = Fraction(sum(contains_forced(final[:i] + final[i + 1:], c, names, rules)
                                         for i, c in enumerate(final)), len(final))
            else:
                memo[key] = Fraction(contains(final, names, rules))
        return memo[key]
    return holds


def query_holds(query: CardQuery, state: GameState) -> Holds:
    return lambda final, _: Fraction(query.accepts(sum(query.filter.test(c, state.rules) for c in final)))


def posterior_by_cards(case: Case, state: GameState) -> dict[tuple, Fraction]:
    """The oracle's posterior over what the face-down cards in the views are: by
    hand index, then those discarded after the sorts, as `Branch.discarded`."""
    latent = sorted(case.hand.index(n) for n in case.down if n in case.hand
                    and any(n in v for v in case.views.values()))
    found: Counter = Counter()
    for what, _, weight in worlds(case):
        found[tuple(what[case.hand[i]] for i in latent) + tuple(what[n] for n in case.gone)] += weight
    total = sum(found.values())
    return {k: v / total for k, v in found.items()}


def ours_by_cards(post) -> dict[tuple, Fraction]:
    found: Counter = Counter()
    for b in post.branches:
        found[tuple(c for _, c in sorted(b.cards, key=lambda ic: ic[0])) + b.discarded] += b.weight
    return {k: v / post.total for k, v in found.items()}


def check(case: Case, discards, targets=(), queries=()) -> None:
    """The posterior, and the odds of each discard, against the oracle; and the
    engine's merged branches against the reference that evaluates each deal."""
    state = case.state()
    post = posterior(state)
    assert post is not None
    assert ours_by_cards(post) == posterior_by_cards(case, state)
    ref = reference(state.observations, state.observed, state.unseen,
                    lambda c: not state.mark or flipped_by_mark(c, state.rules))
    assert ours_by_cards(ref) == ours_by_cards(post)
    for discard in discards:
        for target in targets:
            found = evaluate_discard(state, target, discard)
            assert found.probability == oracle(case, discard, target_holds(target, state)), (target.name, discard)
            one_by_one = engine._over_branches(
                state, discard, ref, None,
                lambda s, t=target, d=discard: engine._evaluate_discard(s, t, d, None, None).probability, None)
            assert one_by_one == found.probability
            assert found.discarded == [state.hand[i] for i in sorted(discard)]  # as the player sees them
        for query in queries:
            found = evaluate_cards(state, query, discard)
            assert found.probability == oracle(case, discard, query_holds(query, state)), (query.text, discard)


# --- The plan's examples -------------------------------------------------------------

def clubs(*ranks: int) -> list[Card]:
    return [card(r, "Clubs") for r in ranks]


def worked_example() -> Case:
    """7♣ 8♣ 9♣ unseen, two of them face down between a 10 and a 6 by rank."""
    seven, eight, nine = clubs(7, 8, 9)
    return Case(hand=["ten", "left", "right", "six"],
                cards={"ten": card(10, "Spades"), "six": card(6, "Hearts"), "left": nine, "right": seven},
                down={"left", "right"}, views={"desc": ["ten", "left", "right", "six"]}, deck=[eight])


def test_the_worked_example():
    case = worked_example()
    state = case.state()
    nine = parse_query("9")
    assert evaluate_cards(state, nine, [2]).probability == 1  # discard the right one
    assert evaluate_cards(state, nine, [1]).probability == Fraction(1, 3)  # the left one
    assert evaluate_cards(state, nine, []).probability == Fraction(2, 3)
    check(case, [[], [1], [2], [1, 2]], queries=[nine, parse_query("at least 1 8")], targets=[get_target("Pair")])


def test_the_hint_example():
    # The hint's lists as independent windows would say 5/7 and 2/7.
    case = Case(hand=["jack", "left", "right", "six"],
                cards={"jack": card(11, "Spades"), "six": card(6, "Hearts"), "left": card(10, "Clubs"),
                       "right": card(8, "Clubs")},
                down={"left", "right"}, views={"desc": ["jack", "left", "right", "six"]}, deck=clubs(7, 9))
    state = case.state()
    ten = parse_query("10")
    assert evaluate_cards(state, ten, [2]).probability == Fraction(3, 4)
    assert evaluate_cards(state, ten, [1]).probability == Fraction(1, 4)
    check(case, [[1], [2]], queries=[ten])


def test_a_card_between_two_spades_by_suit_makes_the_flush():
    spades = [card(r, "Spades") for r in (14, 12, 9, 5)]
    case = Case(hand=["A", "Q", "down", "9", "5"],
                cards={"A": spades[0], "Q": spades[1], "9": spades[2], "5": spades[3], "down": card(10, "Spades")},
                down={"down"}, views={"suit desc": ["A", "Q", "down", "9", "5"]},
                deck=[card(r, s) for r in (2, 3, 4) for s in ("Hearts", "Clubs")] + [card(7, "Spades")])
    state = case.state()
    assert evaluate_discard(state, get_target("Flush"), []).probability == 1
    # Today's odds, which don't know where it is: 2 of the 8 unseen cards are spades.
    plain = engine.with_usable_evidence(state)
    assert plain is state
    assert engine._evaluate_discard(state, get_target("Flush"), [], None, None).probability == Fraction(2, 8)
    # So no draw can miss: can_miss mustn't find one the evidence rules out.
    assert not engine.can_miss(state, get_target("Flush"), [], random.Random(0))
    check(case, [[], [0], [2]], targets=[get_target("Flush"), get_target("Pair")])


# --- Against dealing every way --------------------------------------------------------

def random_case(rng: random.Random, ranks=range(7, 12), copies=False, stones=False, wilds=False,
                hidden_extra=False, gone=False, discarded=False, both=None, latent=(1, 3), face_up=(1, 3),
                down_discarded=0, **kw) -> Case:
    suits = rng.sample(SUITS, 2)
    pool = [card(r, s, "m_wild" if wilds and rng.random() < 0.2 else "c_base") for r in ranks for s in suits]
    rng.shuffle(pool)
    pool = pool[:rng.randint(6, 8)]
    if copies:
        pool += [pool[0], rng.choice(pool)]
    if stones:
        pool += [STONE] * rng.randint(1, 2)
    rng.shuffle(pool)
    cards: dict[str, Card] = {}

    def take(prefix: str, n: int) -> list[str]:
        names = [f"{prefix}{i}" for i in range(n)]
        for name in names:
            cards[name] = pool.pop()
        return names

    up = take("U", rng.randint(*face_up))
    down = take("L", rng.randint(*latent))
    # Face-down cards discarded after some of the sorts: in the first, and each other with even odds.
    after = take("D", down_discarded)
    extra = take("H", 1) if hidden_extra else []
    played = take("G", 1) if gone else []
    thrown = [pool.pop()] if discarded else []
    modes = ["desc", "suit desc"] if both else [rng.choice(["desc", "suit desc"])] if both is False else \
        rng.choice([["desc"], ["suit desc"], ["desc", "suit desc"], ["suit desc", "desc"]])
    # One hidden order of the cards alike in print, the same for every sort.
    tiebreak = {n: rng.randrange(100) for n in cards}
    views = {}
    for k, mode in enumerate(modes):
        shown = up + down + (played if k == 0 else []) + [n for n in after if k == 0 or rng.random() < 0.5]
        views[mode] = sorted(shown, key=lambda n: nominal(printed(cards[n]), mode, tiebreak[n]), reverse=True)
    hand = [n for n in views[modes[-1]] if n not in played and n not in after] + extra
    return Case(hand=hand, cards=cards, down=set(down + extra + after), views=dict(sorted(views.items())),
                deck=pool, discarded=thrown, kw=kw)


TARGETS = [get_target("Pair"), get_target("Two Pair"), get_target("Three of a Kind")]
QUERIES = [parse_query(q) for q in ("9", "at least 2 hearts", "exactly 1 face card", "between 1 and 2 even")]


def some_discards(rng: random.Random, case: Case) -> list[list[int]]:
    n = len(case.hand)
    latent = [i for i, name in enumerate(case.hand) if name in case.down]
    picks = [[], [rng.choice(latent)], sorted(rng.sample(range(n), min(2, n)))]
    return [list(d) for d in dict.fromkeys(tuple(d) for d in picks)]


@pytest.mark.parametrize("seed", range(12))
def test_against_dealing_every_way(seed):
    rng = random.Random(seed)
    case = random_case(rng)
    check(case, some_discards(rng, case), TARGETS, QUERIES)


@pytest.mark.parametrize("seed", range(8))
def test_copies_and_stones_in_both_sorts(seed):
    # Cards alike in print tie: their hidden order is random, but one order for both sorts.
    rng = random.Random(100 + seed)
    case = random_case(rng, copies=True, stones=True, both=True, latent=(2, 3))
    check(case, some_discards(rng, case), TARGETS[:2], QUERIES[:2])


@pytest.mark.parametrize("seed", range(4))
def test_wild_cards_and_smeared(seed):
    rng = random.Random(200 + seed)
    jokers = [{"key": "j_smeared"}] if seed % 2 else []
    case = random_case(rng, wilds=True, ranks=range(7, 10), jokers=jokers)
    flush4 = [{"key": "j_four_fingers"}, *jokers]
    four_fingers = Case(**{**case.__dict__, "kw": {"jokers": flush4}})
    check(four_fingers, some_discards(rng, case), [get_target("Flush"), get_target("Pair")], [QUERIES[1]])


@pytest.mark.parametrize("seed", range(4))
def test_straights_with_four_fingers_and_shortcut(seed):
    # Branches merge by which requirements a card counts towards (_counts_towards), which for
    # these patterns tells most cards apart: checked against the reference and the oracle.
    rng = random.Random(250 + seed)
    case = random_case(rng, ranks=range(6, 12), jokers=[{"key": "j_four_fingers"}, {"key": "j_shortcut"}])
    check(case, some_discards(rng, case), [get_target("Straight"), get_target("Straight Flush"),
                                           get_target("Flush")])


@pytest.mark.parametrize("seed", range(4))
def test_cards_without_evidence_gone_and_discarded(seed):
    # A face-down card in no sort, a face-up card that was played after one, and
    # one discarded face down: all part of the deal, only the sorts' cards narrowed.
    rng = random.Random(300 + seed)
    case = random_case(rng, hidden_extra=True, gone=True, discarded=True, latent=(1, 2))
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:2])


def test_the_house_all_face_down():
    # Keeping them all or throwing them all away doesn't need the sort (_says_nothing_here).
    rng = random.Random(400)
    case = random_case(rng, face_up=(0, 0), latent=(4, 4), both=False)
    assert not set(case.hand) - case.down
    check(case, [[], [0], [3], [0, 1], [0, 1, 2, 3]], TARGETS[:2], QUERIES[:1])
    case.views = {mode: view for mode in ("desc", "suit desc") for view in case.views.values()}
    check(case, [[], [0, 1, 2, 3]], TARGETS[:1], QUERIES[:1])  # two sorts do say something


def test_the_house_first_hand_keeping_it_is_todays_odds(monkeypatch):
    # Eight face-down cards sorted are too many deals to go through, and needn't be.
    deck = [card(r, s) for s in SUITS for r in range(2, 15)]
    names = [f"L{i}" for i in range(8)]
    cards = dict(zip(names, sorted(deck[:8], key=lambda c: nominal(printed(c), "desc"), reverse=True)))
    case = Case(hand=names, cards=cards, down=set(names), views={"desc": names}, deck=deck[8:])
    state = case.state()
    monkeypatch.setattr(engine, "branches", None)  # never called
    for discard in ([], list(range(8))):
        assert (evaluate_discard(state, TARGETS[0], discard).probability
                == engine._evaluate_discard(state, TARGETS[0], discard, None, None).probability)
        assert evaluate_cards(state, QUERIES[0], discard).probability == engine._evaluate_cards(
            state, QUERIES[0], discard).probability


# --- Face-down cards discarded after the sorts ---------------------------------------

@pytest.mark.parametrize("seed", range(10))
def test_cards_discarded_face_down_after_the_sorts(seed):
    # Still where they were seen: they bound the cards around them, and say
    # what's left to draw.
    rng = random.Random(1000 + seed)
    case = random_case(rng, down_discarded=rng.randint(1, 2), latent=(1, 2), both=True if seed % 2 else None)
    assert case.gone
    check(case, some_discards(rng, case), TARGETS[:2], QUERIES)


def only_one_discarded() -> Case:
    """A card discarded face down from between the 9♠ and the 7♥: the 9♦ or the 8♣."""
    return Case(hand=["nine", "seven"], cards={"nine": card(9, "Spades"), "seven": card(7, "Hearts"),
                                              "D": card(8, "Clubs")},
                down={"D"}, views={"desc": ["nine", "D", "seven"]},
                deck=[card(9, "Diamonds"), card(7, "Diamonds"), card(3, "Diamonds")])


def test_a_discarded_card_alone_says_what_is_left_to_draw():
    case = only_one_discarded()
    state = case.state()
    assert all(c.latent is None for c in state.hand) and state.discarded_hidden == 1
    pair = get_target("Pair")
    # The 7♦ is always left to draw, the 9♦ only if the 8♣ was discarded.
    assert evaluate_discard(state, pair, [0]).probability == Fraction(1, 3)
    assert evaluate_discard(state, pair, [1]).probability == Fraction(1, 6)
    # Without the sort they're alike: either is left to draw 3 times in 4, then drawn 1 time in 3.
    plain = engine._evaluate_discard(state, pair, [1], None, None).probability
    assert plain == engine._evaluate_discard(state, pair, [0], None, None).probability == Fraction(1, 4)
    check(case, [[], [0], [1], [0, 1]], [pair], [parse_query("9"), parse_query("at least 1 7")])


def test_the_search_doesnt_relabel_with_a_discarded_card_in_a_sort():
    # Pair can't tell a 9 from a 7, and as many of each are unseen, but they aren't
    # as likely to be left to draw: so only throwing away the 9♠ is best.
    state = only_one_discarded().state()
    pair = get_target("Pair")
    assert check_search(state, pair)[0] == [0]
    found = best_discards(state, pair)
    assert found.options == [[0]] and found.probability == Fraction(1, 3)


def test_the_house_after_discarding_some_face_down():
    # Its one sort, with some of its cards since discarded face down: those kept
    # are the higher or the lower ones, so even keeping the hand needs the deals.
    rng = random.Random(410)
    case = random_case(rng, face_up=(0, 0), latent=(3, 3), down_discarded=2, both=False)
    assert len(case.hand) == 3 and len(case.gone) == 2
    state = case.state()
    assert not engine._says_nothing_here(state, []) and not engine._says_nothing_here(state, [0])
    check(case, [[], [0], [0, 1, 2]], TARGETS[:2], QUERIES[:2])
    # Throwing away the rest too leaves them all gone: today's odds.
    assert engine._says_nothing_here(state, [0, 1, 2])


def test_dna_copies_of_the_higher_of_two_is_not_keeping_it():
    # Two face-down cards sorted, the left one played alone and copied twice: the
    # copies are of the higher card, so it isn't like keeping both.
    case = Case(hand=["L", "R"], cards={"L": card(9, "Clubs"), "R": card(7, "Clubs")}, down={"L", "R"},
                views={"desc": ["L", "R"]}, deck=[card(8, "Clubs"), card(2, "Hearts"), card(3, "Hearts")],
                kw={"discards_left": 0, "hands_left": 2, "dna_copies": 2})
    state = case.state()
    assert not engine._says_nothing_here(state, [0])
    nines = parse_query("at least 2 9")
    # The left one is the 9 in 4 of the 10 pairs, the right one in none.
    assert evaluate_cards(state, nines, [0]).probability == Fraction(4, 10)
    assert evaluate_cards(state, nines, [1]).probability == 0
    check(case, [[0], [1]], [get_target("Three of a Kind")], [nines])


@pytest.mark.parametrize("seed", range(2))
def test_the_mark_with_a_card_discarded_face_down(seed):
    rng = random.Random(520 + seed)
    while True:  # The Mark only flips face cards
        case = random_case(rng, ranks=range(9, 14), blind="The Mark", down_discarded=1, latent=(1, 2))
        state = case.state()
        if all(flipped_by_mark(case.cards[n], state.rules) for n in case.down):
            break
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:3])


@pytest.mark.parametrize("seed", range(4))
def test_the_search_with_cards_discarded_face_down(seed):
    rng = random.Random(950 + seed)
    case = random_case(rng, down_discarded=1, latent=(1, 2), face_up=(2, 3))
    state = case.state()
    for target in (get_target("Pair"), get_target("Three of a Kind"), QUERIES[0], QUERIES[1]):
        check_search(state, target)


@pytest.mark.parametrize("seed", range(4))
def test_the_mark(seed):
    rng = random.Random(500 + seed)
    jokers = [{"key": "j_pareidolia"}] if seed == 3 else []
    while True:  # The Mark only flips face cards
        case = random_case(rng, ranks=range(9, 14), blind="The Mark", jokers=jokers)
        state = case.state()
        if all(flipped_by_mark(case.cards[n], state.rules) for n in case.down):
            break
    assert state.mark
    check(case, some_discards(rng, case), TARGETS[:1], QUERIES[:3])


@pytest.mark.parametrize("blind, kw", [
    ("The Hook", {"discards_left": 0, "hands_left": 2}),
    ("The Serpent", {}),
    ("Cerulean Bell", {}),
    (None, {"discards_left": 0, "hands_left": 2, "dna_copies": 2}),
])
def test_bosses_and_dna(blind, kw):
    rng = random.Random(f"{blind} {kw}")
    case = random_case(rng, blind=blind, **kw)
    if blind == "Cerulean Bell":
        case.forced = next(n for n in case.hand if n not in case.down)
    discards = some_discards(rng, case)
    if blind == "Cerulean Bell":
        discards = [d if case.hand.index(case.forced) in d or not d else sorted({*d, case.hand.index(case.forced)})
                    for d in discards]
    check(case, discards, [get_target("Pair"), combine([get_target("Pair"), get_target("Straight")])], QUERIES[:2])


def test_an_exhausted_deck():
    rng = random.Random(600)
    case = random_case(rng)
    case.deck = case.deck[:1]
    check(case, [[0, 1], [len(case.hand) - 1]], TARGETS[:1], QUERIES[:1])


# --- The best-discard search ---------------------------------------------------------

def test_the_search_throws_away_the_right_face_down_card():
    case = worked_example()
    state = case.state()
    nine = parse_query("9")
    assert check_search(state, nine)[0] == [2]  # the right one: the 9 is the left one or in the deck
    found = best_card_discards(state, nine)
    assert found.probability == 1


@pytest.mark.parametrize("seed", range(6))
def test_the_search_matches_trying_every_discard(seed):
    # Every subset, each with the odds the evidence gives: so options name hand indices.
    rng = random.Random(900 + seed)
    case = random_case(rng, copies=seed % 2 == 1, hidden_extra=seed % 3 == 0, latent=(1, 3), face_up=(2, 3))
    state = case.state()
    for target in (get_target("Pair"), get_target("Three of a Kind"), QUERIES[0], QUERIES[1]):
        check_search(state, target)


# --- Evidence set aside, and what doesn't change ----------------------------------------

def test_no_observations_is_todays_odds():
    case = worked_example()
    case.views = {}
    state = case.state()
    assert posterior(state) is None
    assert state.hand[1].latent is None


def test_evidence_no_deal_fits_is_set_aside(capsys):
    # A 9♣ between two 8s: nothing unseen fits, so the odds are today's, and it's logged once.
    case = worked_example()
    case.cards["ten"] = card(8, "Spades")
    case.views = {"desc": ["ten", "left", "right", "six"]}
    case.cards["six"] = card(8, "Hearts")
    case.cards["right"] = card(8, "Clubs")
    case.deck = clubs(7)
    state = case.state()
    nine = parse_query("9")
    plain = engine._evaluate_cards(state, nine, [2]).probability
    assert posterior(state) is None
    assert evaluate_cards(state, nine, [2]).probability == plain
    unused = engine.with_usable_evidence(state)
    assert unused.observations == () and all(c.latent is None for c in unused.hand)
    evaluate_cards(state, nine, [1])
    assert capsys.readouterr().err.count("fit no deal") == 1


def test_face_up_cards_out_of_order_are_set_aside():
    case = worked_example()
    case.views = {"desc": ["six", "left", "right", "ten"]}
    assert posterior(case.state()) is None


def test_resolving_changes_no_counts():
    case = random_case(random.Random(700), hidden_extra=True, discarded=True, latent=(1, 1), face_up=(1, 1),
                       down_discarded=1)
    state = case.state()
    assert state.discarded_hidden == 2
    for b in posterior(state).branches:
        resolved = engine._resolved(state, b)
        assert len(b.discarded) == 1
        assert (resolved.deck_size, len(resolved.hand)) == (state.deck_size, len(state.hand))
        assert (len(resolved.discard), resolved.discarded_hidden) == (len(state.discard), 1)
        assert sorted(map(repr, resolved.unseen + [c for i, c in b.cards] + list(b.discarded))) == sorted(
            map(repr, state.unseen))
        for discard in ([], [0], [1, 2]):
            assert engine._discard(resolved, discard).draws == engine._discard(state, discard).draws


def test_cancelled():
    cancel = threading.Event()
    cancel.set()
    case = random_case(random.Random(800), latent=(3, 3))
    state = case.state()
    engine._posteriors.clear()
    with pytest.raises(Cancelled):
        posterior(state, cancel)
    with pytest.raises(Cancelled):
        evaluate_cards(state, QUERIES[0], [0], cancel)
    with pytest.raises(Cancelled):
        evaluate_discard(state, TARGETS[0], [0], cancel)
