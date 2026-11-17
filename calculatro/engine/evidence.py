"""What the hand's sorts say about its face-down cards (docs/probability-model.md), and the exact odds with it.

The sorts (`GameState.observations`) narrow what some face-down cards can be. `evaluate_discard` and
`evaluate_cards` average the discarding module's odds over what those cards are, weighed by the posterior
(`posterior`, counted by the posterior module; _averaged), or weigh the draw by windows of cards that
are a uniformly random set of some cards (_windows_for) rather than go through them. Evidence that no
deal fits is set aside (`usable`).

`Session` keeps what's worked out from the evidence from one calculation for the next.
"""

import sys
from collections import OrderedDict
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from fractions import Fraction
from itertools import product
from math import comb, prod
from threading import Event, Lock

from ..cards import CardQuery
from ..hands import ClassKey, Target
from ..posterior import Branch, Deals, Label, Posterior, Window, deals, no_label, printed, sort_value, summed, windows
from ..rules import Rules
from ..state import Card, GameState, ObservedCard
from .counting import Cancelled, Marked, Reuse, _checker, flipped_by_mark
from .discarding import (
    DiscardOutcome,
    _counts_towards,
    _discard,
    _dna_copies,
    _evaluate_cards,
    _evaluate_discard,
    _hooked,
    _rule_classes,
)


class Session:
    """What the engine keeps from one calculation for the next: the app's session (`run_watch` makes one), shared
    by its odds job and its searches, so it's safe between threads. Each entry point takes one as `session`;
    without it, a call keeps what it works out only for itself.

    Everything here is worked out from the state's evidence, so it's for every target and rule; the patterns,
    and what's worked out from them, are in functools caches (`clear_caches`). Each cache keeps only its latest
    few entries.
    """
    usable_kept = 16  # states whose evidence was checked: build_report and the search ask many times
    # Observations no deal fits, already logged: the latest few, so the same ones aren't logged again for every
    # state of the hand. One forgotten is only logged a second time.
    ignored_kept = 16
    # Samplers of deals (_deals_for), shared by every value estimated for a state. Each can take tens of
    # MB (36 MB for The House's first hand sorted both ways), so only the latest few are kept.
    deals_kept = 2
    windows_kept = 16  # _windows_for's windows, by the evidence

    def __init__(self) -> None:
        self.lock = Lock()
        self.usable: OrderedDict[tuple, bool] = OrderedDict()
        self.ignored: OrderedDict[tuple, None] = OrderedDict()
        self.deals: OrderedDict[tuple, Deals | None] = OrderedDict()
        self.windows: OrderedDict[tuple, list[Window]] = OrderedDict()
        # The latest pool's class keys (_pool_keys): (pool, rules, keys). A search's can_miss draws from the same
        # pool (`state.unseen`) for every discard, and working out its keys was most of each call.
        self.pool: tuple[list[Card], Rules, list[ClassKey | None]] | None = None

    def get[V, D](self, cache: OrderedDict[tuple, V], key: tuple, default: D = None) -> V | D:
        """`cache`'s entry for `key`, now its latest; `default` if it has none."""
        with self.lock:
            if key not in cache:
                return default
            cache.move_to_end(key)
            return cache[key]

    def keep[V](self, cache: OrderedDict[tuple, V], key: tuple, value: V, kept: int) -> None:
        """Keeps `value` for `key` as `cache`'s latest entry, and only the latest `kept`."""
        with self.lock:
            cache[key] = value
            cache.move_to_end(key)
            while len(cache) > kept:
                cache.popitem(last=False)



def _own(session: Session | None) -> Session:
    """`session`, or one for a call made without."""
    return Session() if session is None else session



def _evidence_key(state: GameState) -> tuple:
    """What `usable` and `_deals_for` depend on: the sorts, the unseen cards, and the marked cards in none."""
    return state.observations, state.observed, tuple(state.unseen), state.marked



def _slots(state: GameState, observed: Sequence[ObservedCard], extra: Sequence[int],
           skip: Collection[int] = ()) -> tuple[tuple[ObservedCard, ...], int]:
    """The face-down cards at hand indices `extra` as the posterior takes them, and
    `marked_rest`: how many marked face-down cards are neither those nor latent in
    `observed` (bar the ids in `skip`). A marked one is a face card
    (`flipped_by_mark`), as in _mark_split."""
    cards = tuple(ObservedCard(hand=i, marked=state.hand[i].marked) for i in extra)
    enumerated = sum(o.marked for i, o in enumerate(observed) if o.latent and i not in skip)
    return cards, state.marked - enumerated - sum(c.marked for c in cards)



def usable(state: GameState, cancel: Event | None = None, session: Session | None = None) -> bool:
    """Whether some deal of the unseen cards fits `state.observations`.

    Observations that none does (a card changed while face down, say) are set
    aside, logged once a session on stderr: the odds are then today's, with no
    part of that evidence used. Raises Cancelled soon after `cancel` is set.
    """
    if not any(o.latent for o in state.observed):
        return True
    session = _own(session)
    key = _evidence_key(state)
    known = session.get(session.usable, key)
    if known is not None:
        return known
    # Telling no deals apart, the sum is one branch: quick even for The House's first hand.
    _, rest = _slots(state, state.observed, ())
    found = summed(state.observations, state.observed, state.unseen, flipped_by_mark, (), no_label,
                   _checker(cancel), marked_rest=rest).total > 0
    if not found:
        failure = state.observations, state.observed
        with session.lock:
            first = failure not in session.ignored
        session.keep(session.ignored, failure, None, session.ignored_kept)
        if first:
            print("The hand's sorts fit no deal of the unseen cards, so the odds don't use them: "
                  f"{state.observations} of {state.observed}", file=sys.stderr, flush=True)
    session.keep(session.usable, key, found, session.usable_kept)
    return found



_NOT_KEPT = object()  # Session.get's default where None is a value kept



def _deals_for(state: GameState, extra: tuple[int, ...], check: Callable[[], None], session: Session) -> Deals | None:
    """posterior.deals for `state`'s evidence, with the face-down cards at hand
    indices `extra`; None if no deal fits. Built once a session for every value
    an estimate is made of (`build_report` estimates each row, Keep and
    Discard), as it can take a second (The House's first hand sorted both
    ways). A deal that fits makes the evidence usable, so `usable` is answered too."""
    cards, rest = _slots(state, state.observed, extra)
    key = (*_evidence_key(state), cards)
    known = session.get(session.deals, key, _NOT_KEPT)
    if known is not _NOT_KEPT:
        return known
    found = deals(state.observations, state.observed, state.unseen, flipped_by_mark, cards, check, rest)
    session.keep(session.deals, key, found, session.deals_kept)
    if found is not None:
        session.keep(session.usable, _evidence_key(state), True, session.usable_kept)
    return found



def evidence_unchecked(state: GameState, discard: list[int], session: Session) -> bool:
    """Whether the odds of discarding `discard` (evaluate_discard's or
    evaluate_cards') start by checking the hand's sorts (`usable`), and that
    isn't done for `state` in `session` yet. Every such odds checks the same
    thing, so once a try at one has run out of time with this still true, a try
    at another with as little time would too (`build_report`'s quick pass)."""
    if not any(o.latent for o in state.observed) or _says_nothing_here(state, discard):
        return False
    with session.lock:
        return _evidence_key(state) not in session.usable



def evidence_checked(state: GameState, session: Session) -> bool | None:
    """What `usable` has found for `state` in `session`; None if it hasn't been worked out there, or there's
    nothing to check. A worker process tells the app, which tells the others (`note_evidence`), so the check
    is made once between them (pool.ProcessRunner)."""
    if not any(o.latent for o in state.observed):
        return None
    return session.get(session.usable, _evidence_key(state))



def note_evidence(state: GameState, session: Session, found: bool) -> None:
    """Keep in `session` what `usable` found for `state` in another (`evidence_checked`)."""
    session.keep(session.usable, _evidence_key(state), found, session.usable_kept)



def posterior(state: GameState, cancel: Event | None = None, extra: tuple[int, ...] = (),
              label: Label | None = None, skip: frozenset[int] = frozenset(),
              session: Session | None = None) -> Posterior | None:
    """What the face-down cards in `state.observations` are, as a posterior
    (posterior.summed), with the face-down cards at hand indices `extra` too
    (no evidence: see _posterior_for). None if there's nothing to branch on.

    Branches are summed by `label`, what the caller sees of each card (see
    posterior.summed); without it each deal is its own branch. The latent
    cards with ids in `skip` are left out.
    Marked face-down cards are face cards (`_slots`). Observations no deal fits
    are set aside (`usable`).
    Raises Cancelled soon after `cancel` is set.
    """
    observations, observed = state.observations, state.observed
    if not usable(state, cancel, session):
        observations, observed = (), ()
    in_hand = {o.hand for o in observed if o.hand is not None}
    extra = tuple(i for i in extra if i not in in_hand)
    if not any(o.latent for i, o in enumerate(observed) if i not in skip) and not extra:
        return None
    cards, rest = _slots(state, observed, extra, skip)
    found = summed(observations, observed, state.unseen, flipped_by_mark, cards, label, _checker(cancel), skip,
                   marked_rest=rest)
    # No card it can be: only a rank changed under The Mark does that, with DNA's copies alone.
    return found if found.total else None



def with_usable_evidence(state: GameState, cancel: Event | None = None, session: Session | None = None) -> GameState:
    """`state`, or, if its observations are set aside (see `usable`), `state`
    without them, so that its face-down cards are interchangeable again."""
    if usable(state, cancel, session):
        return state
    return replace(state, observations=(), observed=(), hand=[replace(c, latent=None) for c in state.hand])



def _posterior_for(state: GameState, discard: list[int], cancel: Event | None,
                   classes: Callable[[Card], object] | None, session: Session,
                   skip: frozenset[int] = frozenset()) -> Posterior | None:
    """The posterior the odds of discarding `discard` average over, its deals
    summed by what those odds see of them (`classes`, see _labels); every deal
    its own branch without `classes`.

    Besides the evidence, a face-down card played alone as the round's first
    hand is enumerated when DNA makes 2 or more copies of it: they're the same
    card, which isn't like as many separate unseen ones. (One copy is exactly
    the card kept face down, so it needs no branching.)

    The face-down cards with ids in `skip` are left out (a window, _window_for).
    """
    dna = _dna_branch(state, discard)
    label = None if classes is None else _labels(state, discard, dna, classes)
    if _says_nothing_here(state, discard):
        state = replace(state, observations=(), observed=())
    return posterior(state, cancel, () if dna is None else (dna,), label, skip, session)



def _dna_branch(state: GameState, discard: list[int]) -> int | None:
    """The face-down card DNA copies 2 or more times, if any: it's branched on."""
    return discard[0] if _dna_copies(state, discard) >= 2 and state.hand[discard[0]].hidden else None



def _labels(state: GameState, discard: list[int], dna: int | None,
            classes: Callable[[Card], object]) -> Label:
    """What the odds of discarding `discard` see of a face-down card being
    `card`, given where it ends up (posterior.summed's `label`).

    A card kept (or copied by DNA) is part of the final hand: its class
    (`classes`), and whether Cerulean Bell forces it. One gone, thrown away now
    or discarded face down before, is only missing from the cards left to
    draw, so where it went doesn't matter, and nothing about it does if no card
    is drawn. Deals alike in all of that give the same odds: the engine sees
    cards only through their classes, and the unseen cards left are the same
    classes too.
    """
    thrown = set(discard)
    copied = discard[0] if _dna_copies(state, discard) else None  # in the final hand, once or more
    resolved = {o.hand for o in state.observed if o.hand is not None} | ({dna} if dna is not None else set())
    d = _discard(state, discard)
    # Face-down cards the branches don't resolve are drawn like the deck's cards.
    unresolved = sum(1 for i, c in enumerate(state.hand) if c.hidden and i not in thrown and i not in resolved)
    drawn = d.draws + unresolved + (d.added_hidden if dna is None else 0) > 0

    def label(hand: int | None, card: Card) -> object:
        if hand is None or (hand in thrown and hand != copied):
            return ("gone", classes(card)) if drawn else None
        return ("copied" if hand == copied else "kept", state.hand[hand].forced, classes(card))
    return label



def _says_nothing_here(state: GameState, discard: list[int]) -> bool:
    """Whether the evidence can't change the odds of discarding `discard`, so
    that today's odds are exact.

    One sort with no face-up card in it (The House's first hand) says only
    which of its face-down cards is higher than which, not what cards they
    are: every set of cards can be sorted. So it only matters once they meet
    different fates (kept, copied by DNA, or gone: thrown away now or discarded
    face down before), as those are then the higher or the lower ones, or if
    Cerulean Bell forces one of them. Or if only some of them are marked: then
    which are the higher ones says something about which are face cards.
    """
    if len(state.observations) != 1 or not all(o.latent for o in state.observed):
        return False
    if len({o.marked for o in state.observed}) > 1:
        return False
    thrown = set(discard)
    copied = thrown if _dna_copies(state, discard) else set()
    fates = set()
    for o in state.observed:
        if o.hand is not None and state.hand[o.hand].forced:
            return False
        fates.add("gone" if o.discarded or (o.hand in thrown and o.hand not in copied)
                  else "copied" if o.hand in copied else "kept")
    return len(fates) == 1



def _resolved(state: GameState, branch: Branch) -> GameState:
    """`state` with the branch's face-down cards turned up: each takes its
    placeholder's place in hand (selection and Cerulean Bell's force kept), and
    one of it leaves the unseen cards, whether it's kept or discarded. Those
    discarded face down before take the place of one of the discard pile's
    hidden cards each. Deck size, hand size and indices stay as they were."""
    return _resolver(state)(branch)



def _resolver(state: GameState) -> Callable[[Branch], GameState]:
    """_resolved for many branches of `state`: where each unseen card is is found once."""
    places: dict[Card, list[int]] = {}
    for j, card in enumerate(state.unseen):
        places.setdefault(card, []).append(j)
    # Those discarded face down take the place of a hidden card of the discard pile marked alike: the
    # branch's are the observed ones, in order (or, without marks, any).
    hidden = {mark: [i for i, c in enumerate(state.discard) if c.hidden and c.marked == mark] for mark in (False, True)}
    kinds = [o.marked for o in state.observed if o.discarded]
    # A card turned up in the hand keeps its slot's selection and Cerulean Bell's mark.
    # A branch turns up a handful of the few unseen cards, so each is made once.
    turned: dict[tuple[int, Card], Card] = {}

    def resolve(branch: Branch) -> GameState:
        taken: dict[Card, int] = {}
        gone = set()
        for card in [c for _, c in branch.cards] + list(branch.discarded):
            n = taken.get(card, 0)
            gone.add(places[card][n])
            taken[card] = n + 1
        hand = list(state.hand)
        for i, card in branch.cards:
            made = turned.get((i, card))
            if made is None:
                made = turned[i, card] = replace(card, highlighted=hand[i].highlighted, forced=hand[i].forced)
            hand[i] = made
        discard = list(state.discard)
        used = {False: 0, True: 0}
        for j, card in enumerate(branch.discarded):
            mark = j < len(kinds) and kinds[j]
            discard[hidden[mark][used[mark]]] = card
            used[mark] += 1
        unseen = [c for j, c in enumerate(state.unseen) if j not in gone]
        return replace(state, hand=hand, unseen=unseen, discard=discard, observations=(), observed=())
    return resolve



def _averaged(state: GameState, discard: list[int], classes: Callable[[Card], object],
              odds: Callable[[GameState, tuple[Marked, ...]], Fraction], cancel: Event | None,
              session: Session) -> Fraction | None:
    """The odds of discarding `discard`, averaged over what the face-down cards the
    hand's sorts tell about are; None if they tell about none, or say nothing
    here (_says_nothing_here). `odds` evaluates a branch's resolved state, with
    the windows' cards left face down (_windows_for).

    The branches are summed by what the odds see of them (`classes`, _labels),
    so there's one evaluation for each thing they can tell apart, and none at
    all for each of a window's cards.
    """
    windows, splits = _windows_for(state, discard, classes, cancel, session)
    skip = frozenset(i for ids, _ in windows + splits for i in ids)
    marked = tuple(m for _, m in windows)
    post = _posterior_for(state, discard, cancel, classes, session, skip)
    if post is None:
        if not skip:
            return None
        post = Posterior((Branch((), Fraction(1)),), Fraction(1))  # the windows' cards were all there was
    whole = post.total
    for _, options in splits:
        whole *= sum(w for w, _, _ in options)
    total = Fraction(0)
    resolve = _resolver(state)
    for branch in post.branches:
        for chosen in product(*(options for _, options in splits)):
            if cancel is not None and cancel.is_set():
                raise Cancelled
            weight = branch.weight * prod(w for w, _, _ in chosen)
            cards = [c for _, turned, _ in chosen for c in turned]
            resolved = Branch(branch.cards + tuple((i, c) for i, c in cards if i is not None), branch.weight,
                              branch.discarded + tuple(c for i, c in cards if i is None))
            total += weight * odds(resolve(resolved), marked + tuple(m for _, _, ms in chosen for m in ms))
    return total / whole



WINDOW_COUNTS = 64  # most combinations of how many of each window's cards a draw has



SPLITS = 5000  # most ways to split a window by its cards' fates (_split)



# A way to split a window: its weight, the cards it turns up (by hand index, None if
# discarded face down before) and the windows left.
type WindowSplit = tuple[int, tuple[tuple[int | None, Card], ...], tuple[Marked, ...]]



def _windows_for(state: GameState, discard: list[int], classes: Callable[[Card], object],
                 cancel: Event | None, session: Session) -> tuple[list[tuple[frozenset[int], Marked]],
                                                                  list[tuple[frozenset[int], list[WindowSplit]]]]:
    """Windows of `state`'s face-down cards (posterior.windows) for the odds of
    discarding `discard` to weigh the draw by, rather than go through their
    cards: each one's card ids and how the engine sees it.

    A window's cards must all meet one fate, all kept or all gone, as only then
    does what the final hand holds depend on how many of its cards the draw
    has. With marked cards the draw is already weighed (_mark_split), and The Hook
    and Cerulean Bell pick from the final hand at random; DNA's copies of a
    face-down card are branched on. None of those take windows. Of the rest,
    those whose cards the odds tell apart (`classes`) are taken, the most told
    apart first: one whose cards they see alike costs no more to go through.
    The search goes through every combination of how many of each window's
    cards the draw has, so they're taken while those are at most WINDOW_COUNTS.

    A window whose cards meet different fates, side by side in a sort, is split
    instead (_split), if it can be in at most SPLITS ways, and they're likely
    fewer than the deals `summed` would go through: that's the second list,
    with each window's ids and its ways.
    """
    if (state.marked or state.bell or _hooked(state, discard) or _dna_copies(state, discard)
            or _says_nothing_here(state, discard) or not usable(state, cancel, session)):
        return [], []
    key = (state.observations, state.observed, tuple(state.unseen))
    found = session.get(session.windows, key)
    if found is None:
        found = windows(state.observations, state.observed, state.unseen, flipped_by_mark, _checker(cancel))
        session.keep(session.windows, key, found, session.windows_kept)
    thrown = set(discard)
    d = _discard(state, discard)
    unknown = d.draws + d.hidden_kept + d.added_hidden  # cards drawn from the unseen ones, with the kept face-down
    useful = []
    splits = []

    def fate(i: int) -> bool:  # kept
        return state.observed[i].hand is not None and state.observed[i].hand not in thrown

    for w in found:
        fates = {fate(i) for i in w.ids}
        if len(fates) != 1:
            if w.order and (ways := _split(w, [fate(i) for i in w.order], state)) is not None:
                # Each way is a search with a window for each block, which costs about twice what a
                # deal summed by its labels does (posterior.summed): there may be as many as
                # this of those, for each fate's multiset of the labels its cards can have.
                labels = len({classes(c) for c in w.cards})
                kept = sum(map(fate, w.ids))
                deals = comb(labels + kept - 1, kept) * (comb(labels + w.size - kept - 1, w.size - kept)
                                                         if unknown else 1)
                if 2 * len(ways) <= deals:
                    splits.append((w.ids, ways))
            continue
        kept = fates.pop()
        if not kept and not unknown:
            continue  # gone, with nothing drawn: nothing to see of them
        seen = len({classes(c) for c in w.cards})
        if seen > 1:
            # How many of its cards the draw can have.
            counts = min(len(w.cards), unknown) - w.size + 1 if kept else min(len(w.cards) - w.size, unknown) + 1
            useful.append((seen, counts, w, kept))
    taken = []
    options = 1
    for _, counts, w, kept in sorted(useful, key=lambda u: -u[0]):
        if options * counts <= WINDOW_COUNTS:
            options *= counts
            taken.append((w.ids, Marked(w.cards, w.size, kept)))
    return taken, splits



def _split(window: Window, kept: list[bool], state: GameState) -> list[WindowSplit] | None:
    """The ways a window's cards can be, by the cards at the edges of its blocks
    of cards meeting one fate (`kept`, for each card in `window.order`); None if
    there are more than SPLITS.

    The window's cards are a uniform set, sorted into its order, so its blocks
    hold the highest cards, then the next, and so on. Given the lowest card of
    each block but the last, which the odds see turned up, each block's other
    cards are a uniform set of the window's cards between that one and the
    block above's: windows that meet one fate each. A way is as likely as the
    sets it leaves: the product of C(cards between, cards left in the block).
    """
    cards = sorted(window.cards, key=lambda c: sort_value(printed(c), window.mode), reverse=True)
    blocks: list[list[int]] = []  # each block's ids, top first
    fates: list[bool] = []
    for i, k in zip(window.order, kept):
        if fates and fates[-1] == k:
            blocks[-1].append(i)
        else:
            blocks.append([i])
            fates.append(k)
    below = [sum(map(len, blocks[b + 1:])) for b in range(len(blocks))]  # cards the blocks under b need
    found: list[WindowSplit] = []

    def ways(b: int, above: int, weight: int, turned: tuple, left: tuple) -> None:
        """Block b and those under it, the cards up to index `above` being taken."""
        if len(found) > SPLITS:
            return
        block = blocks[b]
        if b == len(blocks) - 1:
            rest = cards[above + 1:]
            found.append((weight * comb(len(rest), len(block)), turned,
                          (*left, Marked(frozenset(rest), len(block), fates[b]))))
            return
        hand = state.observed[block[-1]].hand
        for j in range(above + len(block), len(cards) - below[b]):
            between = cards[above + 1:j]
            more = (Marked(frozenset(between), len(block) - 1, fates[b]),) if len(block) > 1 else ()
            ways(b + 1, j, weight * comb(len(between), len(block) - 1), (*turned, (hand, cards[j])), (*left, *more))

    ways(0, -1, 1, (), ())
    found = [w for w in found if w[0]]
    return found if len(found) <= SPLITS else None



def evaluate_discard(state: GameState, target: Target, discard: list[int],
                     cancel: Event | None = None, reuse: Reuse | None = None,
                     session: Session | None = None) -> DiscardOutcome:
    """Probability of reaching `target` after discarding hand cards at indices `discard`.

    Without discards left they'd have to be played instead, which under The
    Hook also throws away 2 random kept cards (`_hook_taken`), and under
    Crimson Heart changes which joker is debuffed (`rules_after`): the odds
    are then the average over the rules the final hand may be judged under.
    A lone card played as the round's first hand comes back with DNA
    (`_dna_copies`). Calls for the same state can share a `reuse` (see Reuse).

    What the hand's sorts show of its face-down cards (`GameState.observations`)
    is used: the odds are the average over what those cards are, weighed by
    the posterior (`_averaged`).
    """
    def odds(resolved: GameState, windows: tuple[Marked, ...]) -> Fraction:
        return _evaluate_discard(resolved, target, discard, cancel, reuse, windows).probability

    p = _averaged(state, discard, _rule_classes(state, discard, _counts_towards(target, state)), odds, cancel,
                  _own(session))
    if p is None:
        return _evaluate_discard(state, target, discard, cancel, reuse)
    d = _discard(state, discard)
    return DiscardOutcome(d.discarded, d.draws, p)



def evaluate_cards(state: GameState, query: CardQuery, discard: list[int],
                   cancel: Event | None = None, session: Session | None = None) -> DiscardOutcome:
    """Probability that the final hand after discarding satisfies `query`.

    Like evaluate_discard, it averages over what the face-down cards the hand's
    sorts tell about are (`_averaged`). Raises Cancelled soon after
    `cancel` is set.
    """
    def odds(resolved: GameState, windows: tuple[Marked, ...]) -> Fraction:
        return _evaluate_cards(resolved, query, discard, windows).probability

    p = _averaged(state, discard, _rule_classes(state, discard, query.filter.test), odds, cancel, _own(session))
    if p is None:
        return _evaluate_cards(state, query, discard)
    d = _discard(state, discard)
    return DiscardOutcome(d.discarded, d.draws, p)

