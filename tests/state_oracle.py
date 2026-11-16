"""The odds of a state, by dealing every way: an oracle for the engine that doesn't use its model.

It deals the unseen cards to the face-down ones every way, gives the cards alike in print every order of the
game's hidden tie-breakers (the same in both sorts), sorts with the game's get_nominal (`game_order`), keeps the
deals that show what the player saw, and then plays out every draw, after The Hook, DNA, Cerulean Bell and Crimson
Heart. See docs/probability-model.md#face-down-cards-in-the-hands-sorts.
"""

import itertools
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from itertools import pairwise, permutations
from math import comb, factorial, prod

from calculatro.cards import CardQuery
from calculatro.engine import discarding, evaluate_cards, evaluate_discard, evidence, flipped_by_mark, posterior
from calculatro.hands import Target
from calculatro.posterior import Posterior, _branch, latent_ids, sort_value, sortable, tie_factor
from calculatro.posterior import printed as printed_key
from calculatro.rules import Rules
from calculatro.state import SCHEMA, Card, GameState, Observation, ObservedCard
from game_order import nominal
from poker import contains, contains_forced

STONE = Card(enhancement="m_stone")


def as_json(c: Card) -> dict:
    return {"rank": c.rank, "rank_id": c.rank_id, "suit": c.suit, "enhancement": c.enhancement,
            "edition": c.edition, "seal": c.seal, "debuff": c.debuff}


@dataclass
class Case:
    """A hand whose sorts the player saw, and what its face-down cards really are.

    `hand` names its cards in order, `cards` says what every named card is, and
    `down` are the face-down ones: those not in `hand` were discarded face down
    after the sorts. `views` are the sorts seen, by mode, or as a list of (mode,
    names) in the order the mod recorded them, where a mode may repeat: they may
    hold face-up cards no longer in hand, and miss face-down ones (no evidence). `deck` is the
    rest of the unseen cards, `discarded` the cards discarded face down that no
    sort has, and `kw` the snapshot's other fields. `marked` names the face-down
    cards The Mark drew as face cards ("discarded i" for `discarded[i]`).
    """
    hand: list[str]
    cards: dict[str, Card]
    down: set[str]
    views: dict[str, list[str]] | list[tuple[str, list[str]]]
    deck: list[Card]
    discarded: list[Card] = field(default_factory=list)
    forced: str | None = None
    kw: dict = field(default_factory=dict)
    marked: set[str] = field(default_factory=set)

    @property
    def gone(self) -> list[str]:
        """The face-down cards discarded after the sorts, in the order the snapshot gives them ids."""
        return [n for n in self.first_seen() if n in self.down and n not in self.hand]

    @property
    def sorts(self) -> list[tuple[str, list[str]]]:
        """The sorts seen, in the order the snapshot sends them."""
        return list(self.views.items()) if isinstance(self.views, dict) else list(self.views)

    def first_seen(self) -> list[str]:
        return list(dict.fromkeys(n for _, view in self.sorts for n in view))

    @property
    def unseen(self) -> list[Card]:
        return [*self.deck, *(self.cards[n] for n in self.hand if n in self.down),
                *(self.cards[n] for n in self.gone), *self.discarded]

    def state(self) -> GameState:
        """As the mod would export it, parsed."""
        return GameState.from_json(self.snapshot())

    def snapshot(self) -> dict:
        """As the mod would export it."""
        ids: dict[str, int] = {}
        for _, view in self.sorts:
            for name in view:
                ids.setdefault(name, len(ids))

        def marks(name: str) -> dict:
            return {"marked": True} if name in self.marked else {}

        def entry(name: str) -> dict:
            if name in self.down:
                if name not in self.hand:
                    return {"id": ids[name], "discarded": True, **marks(name)}
                return {"id": ids[name], "hand": self.hand.index(name)}
            c = self.cards[name]
            return {"id": ids[name], "stone": True} if c.is_stone else {"id": ids[name], "rank_id": c.rank_id,
                                                                        "suit": c.suit}

        def in_hand(name: str) -> dict:
            out = {"hidden": True, **marks(name)} if name in self.down else as_json(self.cards[name])
            return {**out, "forced": True} if name == self.forced else out

        return {
            "schema": SCHEMA, "in_run": True, "hand_size": len(self.hand), "hands_left": 3, "discards_left": 1,
            "hand": [in_hand(n) for n in self.hand], "unseen": [as_json(c) for c in self.unseen],
            "discard": [{"hidden": True, **marks(n)}
                        for n in [f"discarded {i}" for i in range(len(self.discarded))] + self.gone],
            "observations": [{"mode": m, "cards": [entry(n) for n in v]} for m, v in self.sorts],
            **self.kw}


def printed(c: Card):
    return "Stone" if c.is_stone else (c.rank_id, c.suit)


def chance_seen(views: list[tuple[str, list[str]]], what: dict[str, Card]) -> Fraction:
    """The chance that the game sorts these cards as `views`, (mode, names): cards
    alike in print are in a random order, which is the same in every sort."""
    groups: dict[object, list[str]] = {}
    for name, c in what.items():
        groups.setdefault(printed(c), []).append(name)
    hits = 0
    for orders in itertools.product(*(itertools.permutations(g) for g in groups.values())):
        tiebreak = {name: i for order in orders for i, name in enumerate(order)}
        hits += all(sorted(view, key=lambda n: nominal(printed(what[n]), mode, tiebreak[n]), reverse=True) == view
                    for mode, view in views)
    return Fraction(hits, prod(factorial(len(g)) for g in groups.values()))


# A final hand, whether its first card is forced (Cerulean Bell), and the rules it's judged under.
type Holds = Callable[[list[Card], bool, Rules], Fraction]


def worlds(case: Case) -> list[tuple[dict[str, Card], list[Card], Fraction]]:
    """Every deal of the unseen cards to the face-down ones the player's view
    allows: what each named card is, the deck left, and the deal's weight."""
    unseen = case.unseen
    assert not case.down - set(case.hand) - set(case.gone), "a card discarded face down in no sort"
    slots = ([n for n in case.hand if n in case.down] + case.gone
             + [f"discarded {i}" for i in range(len(case.discarded))])
    shown = case.first_seen()
    out = []
    for picked in itertools.permutations(range(len(unseen)), len(slots)):
        if any(n in case.marked and not face_for_the_mark(unseen[j]) for n, j in zip(slots, picked)):
            continue
        what = {**case.cards, **{n: unseen[j] for n, j in zip(slots, picked)}}
        weight = chance_seen(case.sorts, {n: what[n] for n in shown})
        if weight:
            out.append((what, [c for j, c in enumerate(unseen) if j not in picked], weight))
    return out


def face_for_the_mark(c: Card) -> bool:
    """Whether The Mark draws `c` face down without Pareidolia, as the game's `Card:is_face(true)`: a Jack,
    Queen or King, debuffed or not, but not a Stone card. Its own copy of `engine.flipped_by_mark`."""
    return not c.is_stone and c.rank_id in (11, 12, 13)


type Worlds = list[tuple[dict[str, Card], list[Card], Fraction]]


def oracle(case: Case, discard: list[int], holds: Holds, dealt: Worlds | None = None) -> Fraction:
    """The odds after discarding `discard`, by dealing every way (`worlds`, or the
    deals `dealt`) and drawing every way."""
    state = case.state()
    thrown = {case.hand[i] for i in discard}
    kept = [n for n in case.hand if n not in thrown]
    played = bool(discard) and state.discards_left <= 0  # the selection can only be thrown away by playing it
    psychic = state.blind == "The Psychic" and not state.blind_disabled  # debuffs a first hand of under 5 cards
    copies = state.dna_copies if played and not state.hands_played and len(discard) == 1 and not psychic else 0
    hooked = min(2, len(kept)) if state.hook and played else 0
    judged = crimson_rules(state) if state.crimson and played else [state.rules]
    total = norm = Fraction(0)
    for what, deck, weight in worlds(case) if dealt is None else dealt:
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
                final = [what[n] for n in first + rest] + added + list(drawn)
                outcomes.extend(holds(final, bool(first), rules) for rules in judged)
        total += weight * sum(outcomes) / len(outcomes)
        norm += weight
    return total / norm


def crimson_rules(state: GameState) -> list[Rules]:
    """The rules after Crimson Heart's swap, once for each of its equally likely picks (docs/blinds.md): it lifts
    every joker's debuff but a run-out Perishable's, then debuffs one of the jokers that weren't debuffed (any of
    them, with only one joker)."""
    jokers = state.jokers
    if not jokers:
        return [state.rules]
    picks = [j for j in jokers if not j.debuff] if len(jokers) > 1 else jokers
    if not picks:
        return [Rules.from_jokers(replace(j, debuff=j.perished) for j in jokers)]  # none picked
    return [Rules.from_jokers(replace(j, debuff=j.perished or j is pick) for j in jokers) for pick in picks]


def target_holds(target: Target, state: GameState) -> Holds:
    names = set(target.hands)
    memo: dict[tuple, Fraction] = {}

    def holds(final: list[Card], forced_first: bool, rules: Rules) -> Fraction:
        key = (rules, forced_first, final[0] if forced_first else None,
               frozenset(Counter(final[forced_first:]).items()))
        if key not in memo:
            if forced_first:
                memo[key] = Fraction(contains_forced(final[1:], final[0], names, rules))
            elif not final:
                memo[key] = Fraction(0)  # the whole hand thrown away, and nothing to draw
            elif state.bell:
                memo[key] = Fraction(sum(contains_forced(final[:i] + final[i + 1:], c, names, rules)
                                         for i, c in enumerate(final)), len(final))
            else:
                memo[key] = Fraction(contains(final, names, rules))
        return memo[key]
    return holds


def query_holds(query: CardQuery, state: GameState) -> Holds:
    return lambda final, _, rules: Fraction(query.accepts(sum(query.filter.test(c, rules) for c in final)))


def posterior_by_cards(case: Case, state: GameState, dealt: Worlds | None = None) -> dict[tuple, Fraction]:
    """The oracle's posterior over what the face-down cards in the views are: by
    hand index, then those discarded after the sorts, as `Branch.discarded`."""
    latent = sorted(case.hand.index(n) for n in case.down if n in case.hand
                    and any(n in v for _, v in case.sorts))
    found: Counter = Counter()
    for what, _, weight in worlds(case) if dealt is None else dealt:
        found[tuple(what[case.hand[i]] for i in latent) + tuple(what[n] for n in case.gone)] += weight
    total = sum(found.values())
    return {k: v / total for k, v in found.items()}


def ours_by_cards(post) -> dict[tuple, Fraction]:
    found: Counter = Counter()
    for b in post.branches:
        found[tuple(c for _, c in sorted(b.cards, key=lambda ic: ic[0])) + b.discarded] += b.weight
    return {k: v / post.total for k, v in found.items()}


def reference(observations: Sequence[Observation], observed: Sequence[ObservedCard], unseen: Sequence[Card],
              allowed: Callable[[Card], bool], extra: Sequence[ObservedCard] = (),
              marked_rest: int = 0) -> Posterior:
    """`posterior.summed` without merging anything or pruning early: every
    injective assignment of the unseen cards, one by one, weighed straight from
    the definition. Only for a few cards."""
    ids = latent_ids(observed)
    slots = [observed[card_id] for card_id in ids] + list(extra)
    hand_of: list[int | None] = [slot.hand for slot in slots]
    places = [j for j, card in enumerate(unseen) if sortable(card)]
    faces = sum(map(allowed, unseen)) - sum(slot.marked for slot in slots)
    found = []
    for picked in permutations(places, len(hand_of)):
        if any(slot.marked and not allowed(unseen[j]) for slot, j in zip(slots, picked)):
            continue
        key_of = {card_id: printed_key(card) for card_id, card in enumerate(observed) if not card.latent}
        key_of.update((card_id, printed_key(unseen[j])) for card_id, j in zip(ids, picked))
        if any(sort_value(key_of[a], o.mode) < sort_value(key_of[b], o.mode)
               for o in observations for a, b in pairwise(o.cards)):
            continue
        groups: dict[object, list[int]] = {}
        for card_id, key in key_of.items():
            groups.setdefault(key, []).append(card_id)
        weight = Fraction(1)
        for members in groups.values():
            weight *= tie_factor(members, observations)
        if marked_rest and not all(slot.marked for slot in slots):
            taken = sum(not slot.marked and allowed(unseen[j]) for slot, j in zip(slots, picked))
            weight *= comb(max(0, faces - taken), marked_rest)
        if weight:
            found.append(_branch(hand_of, [unseen[j] for j in picked], weight))
    return Posterior(tuple(found), sum((b.weight for b in found), Fraction(0)))


def over_branches(state: GameState, post: Posterior, odds: Callable[[GameState], Fraction]) -> Fraction:
    """The odds averaged over the posterior's branches, by weight: `odds` of
    each branch's resolved state, every face-down card turned up. With one
    branch for each deal (`reference`), it's what `evidence._averaged` is checked against."""
    total = Fraction(0)
    resolve = evidence._resolver(state)
    for branch in post.branches:
        total += branch.weight * odds(resolve(branch))
    return total / post.total


def check(case: Case, discards, targets=(), queries=()) -> None:
    """The posterior, and the odds of each discard, against the oracle; and the
    engine's merged branches against the reference that evaluates each deal.
    Without sorts (no `views`), only the odds."""
    state = case.state()
    ref = None
    if case.views:
        post = posterior(state)
        assert post is not None
        assert ours_by_cards(post) == posterior_by_cards(case, state)
        ref = reference(state.observations, state.observed, state.unseen, flipped_by_mark, (),
                        evidence._slots(state, state.observed, ())[1])
        assert ours_by_cards(ref) == ours_by_cards(post)
    for discard in discards:
        for target in targets:
            found = evaluate_discard(state, target, discard)
            assert found.probability == oracle(case, discard, target_holds(target, state)), (target.name, discard)
            if ref is not None:
                # DNA's copies of a face-down card in no sort: enumerated too, as the engine does.
                dna = evidence._dna_branch(state, discard)
                in_sorts = {o.hand for o in state.observed}
                ref_here = ref if dna is None or dna in in_sorts else reference(
                    state.observations, state.observed, state.unseen, flipped_by_mark,
                    *evidence._slots(state, state.observed, (dna,)))
                one_by_one = over_branches(
                    state, ref_here,
                    lambda s, t=target, d=discard: discarding._evaluate_discard(s, t, d, None, None).probability)
                assert one_by_one == found.probability
            assert found.discarded == [state.hand[i] for i in sorted(discard)]  # as the player sees them
        for query in queries:
            found = evaluate_cards(state, query, discard)
            assert found.probability == oracle(case, discard, query_holds(query, state)), (query.text, discard)
