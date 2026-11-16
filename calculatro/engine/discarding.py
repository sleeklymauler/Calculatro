"""What a discard does, and its exact odds when every face-down card is unknown.

_discard works out what's kept, what's drawn and from which pool. _finals lists every way the final
hand can then be made, weighted: under each set of rules Crimson Heart may leave, after The Hook takes
kept cards, with DNA's copies, The Mark's marked cards and Cerulean Bell's forced card. The exact odds
(_evaluate_discard, _evaluate_cards) count the draws for each with the counting module, and the estimates
(sampling._FinalHands) draw from the same list, so a rule about what the final hand holds is written
once, here.

The evidence module averages these odds over what the face-down cards the hand's sorts tell about are,
calling _evaluate_discard and _evaluate_cards for each thing they can be.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import cache
from math import comb, prod
from threading import Event

from ..cards import CardQuery
from ..hands import ClassKey, Target, bell_matters, card_key, forced_patterns
from ..rules import Rules
from ..state import Card, GameState
from .counting import Cancelled, Marked, Reuse, count_probability, flipped_by_mark, probability


@dataclass(frozen=True)
class DiscardOutcome:
    discarded: list[Card]
    draws: int  # cards drawn from the deck after discarding
    probability: Fraction



@dataclass(frozen=True)
class _Discard:
    discarded: list[Card]
    kept_known: list[Card]
    hidden_kept: int
    draws: int
    pool: list[Card]  # unseen cards: undrawn deck + face-down cards in hand or discarded
    marked: int  # face-down cards known to be face cards (The Mark): in hand, discarded now or before
    marked_kept: int = 0  # of `hidden_kept`, the marked ones
    hooked: int = 0  # kept cards The Hook throws away at random before the draw
    added: tuple[Card, ...] = ()  # DNA's copies in the final hand, which The Hook can't take
    added_hidden: int = 0  # DNA's copies of a face-down card
    added_marked: bool = False  # and it's marked



def _hooked(state: GameState, discard: list[int]) -> int:
    """How many kept cards The Hook throws away before the draw: 2 (or all there
    are) when the selection is played, which it has to be without discards left."""
    if not (state.hook and discard and state.discards_left <= 0):
        return 0
    return min(2, len(state.hand) - len(set(discard)))



def _dna_copies(state: GameState, discard: list[int]) -> int:
    """How many copies of the selection DNA puts in the hand: when it's a lone
    card played as the round's first hand, which it has to be without discards
    left. Not if the hand is debuffed, which only The Psychic does to a first
    hand (`evaluate_play` skips the jokers' `before` step)."""
    if state.discards_left > 0 or state.hands_played or len(set(discard)) != 1:
        return 0
    if state.blind == "The Psychic" and not state.blind_disabled:
        return 0
    return state.dna_copies



def _discard(state: GameState, discard: list[int]) -> _Discard:
    discard_set = set(discard)
    hooked = _hooked(state, discard)
    copies = _dna_copies(state, discard)
    played = state.hand[discard[0]] if copies else None
    kept = [c for i, c in enumerate(state.hand) if i not in discard_set]
    kept_known = [c for c in kept if not c.hidden]
    if not discard:
        # The game only draws after a play or discard (or at the round's start,
        # but odds are only asked for once the hand is dealt), so a hand left
        # short by a consumable or a bigger hand size stays short.
        draws = 0
    elif state.serpent:
        draws = 3  # however many were discarded, and even past the hand size
    else:
        # DNA's copies go straight into the hand, taking up room the draw would fill.
        draws = state.hand_size - len(kept) + hooked - copies
    draws = max(0, min(draws, state.deck_size))
    # One copy of a face-down card is exactly that card kept face down. Several
    # are the same unknown card, so evaluate_discard branches on what it is
    # (_posterior_for) and they arrive here as known cards.
    added_hidden = copies if played is not None and played.hidden else 0
    return _Discard([state.hand[i] for i in sorted(discard_set)], kept_known,
                    len(kept) - len(kept_known), draws, state.unseen, state.marked,
                    sum(c.hidden and c.marked for c in kept), hooked,
                    () if added_hidden or played is None else (played,) * copies, added_hidden,
                    bool(added_hidden) and played.marked)



def discard_draws(state: GameState, discard: list[int]) -> int:
    """How many cards are drawn after discarding the hand cards at `discard`."""
    return _discard(state, discard).draws



def rules_after(state: GameState, discard: list[int]) -> list[tuple[Rules, int]]:
    """The rules the final hand is judged under after discarding `discard`,
    each with how many of the equally likely ways lead to it.

    Only Crimson Heart changes them, and only after a hand is played, which
    the selection has to be without discards left: after the draw
    (`Blind:drawn_to_hand`), it lifts every joker's debuff, bar a Perishable
    one's that has run out (`Card:set_debuff`), then debuffs one of the jokers
    that weren't debuffed, uniformly at random (any of them with only one).
    The draw itself is unchanged, and so is its size: a hand-size joker's
    bonus comes and goes after it.
    """
    jokers = state.jokers
    if not (discard and state.discards_left <= 0 and state.crimson and jokers):
        return [(state.rules, 1)]
    picks = [i for i, j in enumerate(jokers) if not j.debuff] if len(jokers) > 1 else [0]
    if not picks:
        picks = [None]  # every joker debuffed: none is picked, and only the perished stay so
    outcomes = Counter(Rules.from_jokers(replace(j, debuff=j.perished or i == pick) for i, j in enumerate(jokers))
                       for pick in picks)
    return list(outcomes.items())



def _merged(outcomes: list[tuple[Rules, int]], same: Callable[[Rules], object]) -> list[tuple[Rules, int]]:
    """`outcomes` with the rules that give the same `same` (and so the same odds) merged."""
    merged: dict[object, tuple[Rules, int]] = {}
    for rules, n in outcomes:
        key = same(rules)
        first, m = merged.get(key, (rules, 0))
        merged[key] = first, m + n
    return list(merged.values())



@dataclass(frozen=True)
class _Final:
    """One way the final hand after a discard can be made (_finals), which
    `weight` of the equally likely ways lead to, judged under `rules`.

    It holds the `known` cards, `unseen` cards of the pool (the face-down kept
    cards, a face-down card DNA copies, and the draw) and `copies` more of that
    copied card. The unseen ones are a uniform sample of the pool, but for
    `marked_held` of them, the marked face-down cards (The Mark): a uniform
    sample of the pool's face cards, with the other marked ones gone from it
    (_mark_split). `copy_marked` if the copied card is one of those.

    With `bell`, Cerulean Bell forces a card of the hand: `forced`, kept and
    not among the `known`, or else one of the hand at random (_bell_after_draw).
    """
    weight: int
    rules: Rules
    known: tuple[Card, ...]
    unseen: int
    marked_held: int = 0
    copies: int = 0
    copy_marked: bool = False
    bell: bool = False
    forced: Card | None = None



def _finals(state: GameState, discard: list[int], d: _Discard, bell: Callable[[Rules], bool],
            same_rules: Callable[[Rules], object], same_card: Callable[[Card, Rules], object]) -> list[_Final]:
    """Every way the final hand after discarding `discard` (`d`, _discard's) can
    be made, as the engine models the game dealing it: under each set of rules
    it may be judged by (`rules_after`, those alike in `same_rules` merged), and
    with The Hook, after each way it takes kept cards (_hook_taken).

    The exact odds (_discard_odds, _card_odds) and the sampled ones
    (_FinalHands) both start from these, so a rule about what the final hand
    holds belongs here, written once, and only how the unseen cards are drawn
    has an exact version (`probability`, `count_probability`) and a sampled one
    (_drawer).

    `bell(rules)` says whether Cerulean Bell's forced card matters to the odds;
    `same_card`, which kept cards are alike to them (for The Hook).
    """
    outcomes = rules_after(state, discard)
    if len(outcomes) > 1:
        outcomes = _merged(outcomes, same_rules)
    copied = min(1, d.added_hidden)  # DNA's copies of a face-down card are one unseen card, repeated
    copy_marked = bool(copied) and d.added_marked
    forced = state.forced
    found = []
    for rules, n in outcomes:
        forcing = bell(rules)
        if forcing and not discard and forced is not None and not state.hand[forced].hidden:
            # Keeping the hand keeps its forced card.
            known = tuple(c for i, c in enumerate(state.hand) if i != forced and not c.hidden)
            found.append(_Final(n, rules, known, d.hidden_kept + d.draws, bell=True, forced=state.hand[forced]))
            continue
        for w, known, hidden, marked in _hook_taken(d, rules, same_card):
            found.append(_Final(n * w, rules, (*known, *d.added), hidden + copied + d.draws, marked + copy_marked,
                                d.added_hidden - copied, copy_marked, forcing))
    return found



def _hook_taken(d: _Discard, rules: Rules,
                same_card: Callable[[Card, Rules], object]) -> list[tuple[int, list[Card], int, int]]:
    """The kept cards left once The Hook has thrown `d.hooked` of them away,
    uniformly at random, before the draw (`Blind:press_play`), each way with
    its weight: (weight, known cards, face-down cards, marked ones of those).
    Without The Hook, the kept cards. Kept cards alike in `same_card` leave
    alike hands, so it goes by how many of each kind it takes (face-down cards
    being two kinds: marked or not)."""
    if not d.hooked:
        return [(1, d.kept_known, d.hidden_kept, d.marked_kept)]
    groups: dict[object, list[Card]] = {}
    for c in d.kept_known:
        groups.setdefault(same_card(c, rules), []).append(c)
    sizes = [len(g) for g in groups.values()] + [d.marked_kept, d.hidden_kept - d.marked_kept]

    def taken(i: int, left: int) -> Iterator[tuple[int, ...]]:
        if i == len(sizes):
            if not left:
                yield ()
            return
        for k in range(min(sizes[i], left) + 1):
            for rest in taken(i + 1, left - k):
                yield (k, *rest)

    return [(prod(comb(size, k) for size, k in zip(sizes, counts)),
             [c for g, k in zip(groups.values(), counts) for c in g[k:]],
             d.hidden_kept - counts[-2] - counts[-1], d.marked_kept - counts[-2])
            for counts in taken(0, d.hooked)]



def _bell_for(target: Target, state: GameState) -> Callable[[Rules], bool]:
    """Whether Cerulean Bell's forced card matters to `target` under some rules."""
    return lambda rules: state.bell and bell_matters(target, rules)



def _same_for(target: Target) -> Callable[[Rules], object]:
    """What decides `target`'s odds of the rules: its patterns, and with Smeared the cards' classes too.
    (Crimson Heart is the boss, so The Mark's split and Cerulean Bell don't apply.)"""
    return lambda rules: (target.patterns(rules), rules.smeared)



def _rule_classes(state: GameState, discard: list[int],
                  test: Callable[[Card, Rules], object]) -> Callable[[Card], object]:
    """What the odds see of a resolved card: `test` (its class, or whether it
    matches a query) under each set of rules the final hand may be judged under,
    and with marked cards whether it's a face card."""
    rule_sets = list(dict.fromkeys([state.rules, *(rules for rules, _ in rules_after(state, discard))]))
    marked = state.marked > 0
    seen: dict[Card, object] = {}

    def classes(card: Card) -> object:
        found = seen.get(card)
        if found is None:
            found = seen[card] = (tuple(test(card, rules) for rules in rule_sets), marked and flipped_by_mark(card))
        return found
    return classes



def _counts_towards(target: Target, state: GameState) -> Callable[[Card, Rules], object]:
    """A card's class as far as `target` can tell: which requirements of its
    patterns it counts towards. Two cards alike in that are interchangeable
    anywhere, kept or among the cards left to draw, since swapping them meets
    and misses the same patterns (as in `search._groups`). Under Cerulean Bell
    the forced card's own class can matter (`_bell_after_draw`), so there it's
    the card's class itself."""
    if state.bell and bell_matters(target, state.rules):
        return card_key

    def test(card: Card, rules: Rules) -> object:
        return _memberships(target.hands, rules).get(card_key(card, rules), frozenset())
    return test



@cache
def _memberships(hands: tuple[str, ...], rules: Rules) -> dict[ClassKey, frozenset[tuple[int, int]]]:
    """For each class, the requirements of the hands' patterns it counts towards, as (pattern, requirement)."""
    found: dict[ClassKey, set[tuple[int, int]]] = defaultdict(set)
    for p, pattern in enumerate(Target("", hands).patterns(rules)):
        for q, req in enumerate(pattern):
            for key in req.keys:
                found[key].add((p, q))
    return {key: frozenset(pq) for key, pq in found.items()}



def _evaluate_discard(state: GameState, target: Target, discard: list[int],
                      cancel: Event | None, reuse: Reuse | None, windows: Sequence[Marked] = ()) -> DiscardOutcome:
    """evaluate_discard with every face-down card unknown, or resolved by a
    branch, but for those in the `windows`."""
    d = _discard(state, discard)
    # Rules the target's patterns don't see give the same odds (_same_for): Shortcut for a Flush, or any of
    # them for a Pair.
    finals = _finals(state, discard, d, _bell_for(target, state), _same_for(target), card_key)
    p = sum((f.weight * _discard_odds(target, d, f, cancel, reuse, windows) for f in finals),
            Fraction(0)) / sum(f.weight for f in finals)
    return DiscardOutcome(d.discarded, d.draws, p)



def _discard_odds(target: Target, d: _Discard, f: _Final, cancel: Event | None, reuse: Reuse | None,
                  windows: Sequence[Marked] = ()) -> Fraction:
    """evaluate_discard's odds of the final hand made as `f` is.
    `windows` only come without The Hook, Cerulean Bell or The Mark (_windows_for)."""
    # DNA's copies of a face-down card, if more than one, are branched on (_dna_branch), so they only come
    # here where no card can be the one copied: then they're counted as that many unseen cards.
    unseen, marked_held = f.unseen + f.copies, f.marked_held + f.copies * f.copy_marked
    known = list(f.known)
    if f.forced is not None:
        return probability(target, known, d.pool, unseen, f.rules, cancel, forced=f.forced, reuse=reuse)
    if f.bell:
        return _bell_after_draw(target, known, d.pool, unseen, f.rules, cancel, reuse)
    return probability(target, known, d.pool, unseen, f.rules, cancel, d.marked, marked_held, reuse=reuse,
                       windows=windows)



def _bell_after_draw(target: Target, kept: list[Card], pool: list[Card], draws: int,
                     rules: Rules, cancel: Event | None, reuse: Reuse | None = None) -> Fraction:
    """P(`target` can be played) when Cerulean Bell forces a random card of the final hand.

    Playing or discarding clears the forced card, and after the draw the game
    forces a uniformly random card of the new hand (`Blind:drawn_to_hand`).
    So the probability is the average over the final hand's cards of the
    probability with that card forced: each kept card, and `draws` times a
    random unseen one, which is any pool card with the others drawn from the rest.

    Cards that the forced patterns can't tell apart give the same result, so
    one search covers each group (_forced_group).
    """
    draws = max(0, min(draws, len(pool)))
    size = len(kept) + draws
    if not size:
        return Fraction(0)
    total = Fraction(0)
    done: dict[object, Fraction] = {}
    for i, c in enumerate(kept):
        key = card_key(c, rules)
        if key not in done:
            done[key] = probability(target, kept[:i] + kept[i + 1:], pool, draws, rules, cancel, forced=c,
                                   reuse=reuse)
        total += done[key]
    if draws:
        groups: dict[object, list[int]] = {}
        for j, c in enumerate(pool):
            # The first time, each card's forced patterns are built: up to 7 ms each, 120 ms for all
            # (Four Fingers + Shortcut, Full House + Straight Flush), so check between them.
            if cancel is not None and cancel.is_set():
                raise Cancelled
            groups.setdefault(_forced_group(target.hands, rules, card_key(c, rules)), []).append(j)
        unseen = Fraction(0)
        for members in groups.values():
            j = members[0]
            unseen += len(members) * probability(target, kept, pool[:j] + pool[j + 1:], draws - 1, rules,
                                                 cancel, forced=pool[j], reuse=reuse)
        total += draws * unseen / len(pool)
    return total / size



@cache
def _forced_group(hands: tuple[str, ...], rules: Rules, key: ClassKey | None) -> tuple:
    """What decides the odds with a forced card of class `key` drawn from the pool:
    its forced patterns, and which of their requirements it would count towards
    if it stayed in the pool (one card fewer there for those)."""
    patterns = forced_patterns(Target("", hands), rules, key)
    return patterns, frozenset((p, q) for p, pattern in enumerate(patterns)
                               for q, req in enumerate(pattern) if key in req.keys)



def _evaluate_cards(state: GameState, query: CardQuery, discard: list[int],
                    windows: Sequence[Marked] = ()) -> DiscardOutcome:
    """evaluate_cards with every face-down card unknown, or resolved by a branch.

    The final hand is the kept cards and the draw. From the player's view the
    face-down kept cards and the draw are a uniform sample of the unseen pool,
    except with The Mark: its face-down cards are face cards, so fewer of
    those are left to draw. Cerulean Bell's forced card is in the final hand
    like any other, so it changes nothing. Without discards left the selection
    would be played instead, which under The Hook also throws away 2 random
    kept cards (`_hooked`): the odds are the average over which ones, and a lone
    card played as the round's first hand comes back with DNA (`_dna_copies`).
    Under Crimson Heart, Pareidolia may be debuffed or not after the play
    (`rules_after`), which changes the face cards: the odds are the average
    over the rules.
    """
    d = _discard(state, discard)
    # The odds only see which cards match: Four Fingers and Shortcut never change that, and Smeared and
    # Pareidolia only for some queries. Cerulean Bell's forced card is in the hand like any other.
    cards = (*d.kept_known, *d.added, *d.pool)
    finals = _finals(state, discard, d, lambda rules: False,
                     lambda rules: tuple(query.filter.test(c, rules) for c in cards), query.filter.test)
    p = sum((f.weight * _card_odds(query, d, f, windows) for f in finals), Fraction(0)) / sum(f.weight for f in finals)
    return DiscardOutcome(d.discarded, d.draws, p)



def _card_odds(query: CardQuery, d: _Discard, f: _Final, windows: Sequence[Marked] = ()) -> Fraction:
    """evaluate_cards's odds of the final hand made as `f` is. `windows` only
    come without The Hook or The Mark (_windows_for). DNA's copies of a
    face-down card are counted as _discard_odds counts them."""
    have = sum(query.filter.test(c, f.rules) for c in f.known)
    return count_probability(query, d.pool, f.unseen + f.copies, f.rules, d.marked,
                             f.marked_held + f.copies * f.copy_marked, have, windows)

