"""Estimates, while the exact odds are slow (docs/probability-model.md#approximations).

`estimate_discard` and `estimate_cards` take the exact odds of deals of the face-down cards the hand's
sorts tell about, drawn at random (_estimate), or else make final hands at random (_estimate_draws, by
_FinalHands from the discarding module's _finals). Each gives an `Estimate`, a range that's never shown as
exact (`report.Approx`). `can_miss` draws final hands the same way, to show quickly that a discard's
odds are below 1.
"""

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from functools import cached_property
from itertools import accumulate
from math import sqrt
from random import Random
from threading import Event
from time import monotonic

from ..cards import CardQuery
from ..hands import ClassKey, Target, card_key
from ..rules import Rules
from ..state import Card, GameState
from .counting import Cancelled, _checker, _target_test, flipped_by_mark
from .discarding import (
    _bell_for,
    _counts_towards,
    _discard,
    _evaluate_cards,
    _evaluate_discard,
    _finals,
    _rule_classes,
    _same_for,
)
from .evidence import (
    Session,
    _deals_for,
    _dna_branch,
    _labels,
    _own,
    _posterior_for,
    _resolved,
    _resolver,
    _says_nothing_here,
)


@dataclass(frozen=True)
class Estimate:
    """Odds estimated from `samples` deals of the face-down cards (_estimate),
    or, if `draws`, from `samples` final hands drawn at random (_estimate_draws):
    `mean`, and the range from `low` to `high` they're likely in. Not certain:
    the range is MARGIN standard errors either side."""
    mean: float
    low: float
    high: float
    samples: int
    draws: bool = False



MARGIN = 3  # standard errors either side of an estimate



ESTIMATE_SAMPLES = 4096  # most deals an estimate draws



DRAW_SAMPLES = 1 << 17  # most final hands an estimate from draws makes



ESTIMATE_CLOSE = 0.005  # an estimate stops once its range is this close either side (and it has 64 deals)



def _sampled(sample: Callable[[], float], check: Callable[[], None], seconds: float, most: int,
             draws: bool = False) -> Estimate:
    """An Estimate from the values `sample` gives, each unbiased, taken until
    the range is within ESTIMATE_CLOSE either side (with at least 64), or
    there are `most`, or `seconds` have passed. The range counts a value of 0
    and one of 1 besides those taken, so a few all alike can't make it look
    certain."""
    deadline = monotonic() + seconds
    n, total, squares = 0, 0.0, 0.0

    def margin() -> float:
        m, s, q = n + 2, total + 1, squares + 1
        return MARGIN * sqrt(max(0.0, q - s * s / m) / (m - 1) / m)

    while True:
        check()
        p = sample()
        n, total, squares = n + 1, total + p, squares + p * p
        if n >= most or (n >= 64 and margin() <= ESTIMATE_CLOSE) or monotonic() >= deadline:
            break
    mean, spread = total / n, margin()
    return Estimate(mean, max(0.0, mean - spread), min(1.0, mean + spread), n, draws)



class _Until:
    """A `cancel` for the engine that's set once `cancel` is, or `deadline` (monotonic) has passed."""

    def __init__(self, cancel: Event | None, deadline: float):
        self.cancel, self.deadline = cancel, deadline

    def is_set(self) -> bool:
        return (self.cancel is not None and self.cancel.is_set()) or monotonic() >= self.deadline



DEAL_SHARE = 200  # an estimate gives each deal's exact odds 1/DEAL_SHARE of its time



def _estimate(state: GameState, discard: list[int], classes: Callable[[Card], object],
              odds: Callable[[GameState, Event | _Until | None], Fraction], cancel: Event | None,
              seconds: float, session: Session, hands: Callable[[GameState], Callable[[Random], bool]] | None = None
              ) -> Estimate | None:
    """The odds of discarding `discard` estimated from deals of the face-down
    cards the hand's sorts tell about, drawn at random by their weight in the
    posterior (posterior.deals); None if there are none, as for _averaged.

    Each deal's odds are exact, and usually quick, with every face-down card
    turned up, so the estimate is off only by which deals were drawn. Deals
    alike in what the odds see of them (`classes`, _labels) share an
    evaluation. With `hands`, a deal's exact odds that take more than
    1/DEAL_SHARE of the time are given up, and so are every other deal's
    from then on, which would be as slow: each time such a deal is drawn,
    it's one final hand drawn at random (`hands` of its resolved state) that
    makes the target or not. Slow targets (Four Fingers with Shortcut) would
    leave too few deals otherwise. Either way each value drawn is unbiased.

    It draws until its range is within ESTIMATE_CLOSE either side, or it has
    ESTIMATE_SAMPLES (DRAW_SAMPLES with `hands`), or `seconds` have passed.
    The draws are seeded, so the same state gives the same estimate, unless
    the time runs out first.

    The range counts a deal at 0 and a deal at 1 besides those drawn, so a
    few deals all alike can't make it look certain.

    `seconds` includes building the sampler of deals (_deals_for), but the
    drawing gets at least a quarter of it.
    """
    start = monotonic()
    if _says_nothing_here(state, discard) or not any(o.latent for o in state.observed):
        return None
    check = _checker(cancel)
    dna = _dna_branch(state, discard)
    in_hand = {o.hand for o in state.observed if o.hand is not None}
    extra = () if dna is None or dna in in_hand else (dna,)
    # None as well if the evidence is set aside (`usable`): no deal fits it.
    found = _deals_for(state, extra, check, session)
    if found is None:
        return None
    label = _labels(state, discard, dna, classes)
    resolve = _resolver(state)
    rng = Random(0)
    seen: dict[frozenset, float] = {}
    slow: dict[frozenset, Callable[[Random], bool]] = {}  # deals whose exact odds were given up, with `hands`
    exact = True  # until a deal's exact odds are given up

    def sample() -> float:
        nonlocal exact
        branch = found.draw(rng)
        key = frozenset(Counter(label(i, c) for i, c in [*branch.cards, *((None, c) for c in branch.discarded)])
                        .items())
        p = seen.get(key)
        if p is not None:
            return p
        if key not in slow:
            if hands is None:
                p = seen[key] = float(odds(resolve(branch), cancel))
                return p
            if exact:
                try:
                    p = seen[key] = float(odds(resolve(branch), _Until(cancel, monotonic() + seconds / DEAL_SHARE)))
                    return p
                except Cancelled:
                    check()  # the estimate itself cancelled
                    exact = False
            slow[key] = hands(resolve(branch))
        return float(slow[key](rng))
    left = max(seconds / 4, seconds - (monotonic() - start))
    return _sampled(sample, check, left, ESTIMATE_SAMPLES if hands is None else DRAW_SAMPLES)



def estimate_discard(state: GameState, target: Target, discard: list[int], cancel: Event | None = None,
                     seconds: float = 1.0, most: int = DRAW_SAMPLES, session: Session | None = None) -> Estimate:
    """evaluate_discard's odds estimated in about `seconds`: from deals of the
    face-down cards the hand's sorts tell about (_estimate), or where they
    tell about none here, from final hands drawn at random (_estimate_draws,
    at most `most`). Raises Cancelled soon after `cancel` is set."""
    def odds(resolved: GameState, until: Event | _Until | None) -> Fraction:
        return _evaluate_discard(resolved, target, discard, until, None).probability

    classes = _rule_classes(state, discard, _counts_towards(target, state))
    session = _own(session)
    found = _estimate(state, discard, classes, odds, cancel, seconds, session,
                      lambda resolved: _final_hands(resolved, target, discard, session))
    return found if found is not None else _estimate_draws(state, target, discard, cancel, seconds, most, session)



def _estimate_draws(state: GameState, target: Target, discard: list[int], cancel: Event | None,
                    seconds: float, most: int = DRAW_SAMPLES, session: Session | None = None) -> Estimate:
    """evaluate_discard's odds without the hand's sorts (`_evaluate_discard`'s),
    estimated from final hands drawn at random (_final_hands): the share that
    make `target`. Seeded, so the same state gives the same estimate, unless
    the time runs out first. For the odds that are slow to count however few
    the face-down cards are (Four Fingers with Shortcut, The Mark)."""
    final = _final_hands(state, target, discard, session)
    rng = Random(0)
    return _sampled(lambda: float(final(rng)), _checker(cancel), seconds, most, draws=True)



def _pool_keys(pool: list[Card], rules: Rules, session: Session) -> list[ClassKey | None]:
    """[card_key(c, rules) for c in pool], kept for the same list (by identity, which holding it keeps)
    and rules as the session's last time (`Session.pool`). Nothing changes a pool once made."""
    last = session.pool  # read once: another thread may replace it, never change it
    if last is not None and last[0] is pool and last[1] == rules:
        return last[2]
    keys = [card_key(c, rules) for c in pool]
    session.pool = pool, rules, keys
    return keys



def _final_hands(state: GameState, target: Target, discard: list[int],
                 session: Session | None = None) -> _FinalHands:
    """Draws a final hand after discarding `discard` at random, as the engine
    models the game dealing it, and says whether it makes `target`: so the
    chance it says yes is `_evaluate_discard`'s odds. Every face-down card is
    unknown (the hand's sorts aren't used)."""
    return _FinalHands(state, target, discard, _own(session))



def _drawer(pool: list[Card], marked: int) -> Callable[[Random, int, int], tuple[list[int], list[int]]]:
    """The sampled version of how `probability` and `count_probability` draw:
    `draw(rng, n, held)` picks `n` cards of `pool`, by index, of which `held`
    are marked face-down cards (The Mark), and gives (those, the others). The
    pool's `marked` face-down cards are a uniform sample of its face cards,
    and the others come from what's left (_mark_split); without them, all `n`
    are a uniform sample of the pool."""
    everything = range(len(pool))
    faces = [j for j, c in enumerate(pool) if flipped_by_mark(c)] if marked else []
    if len(faces) < marked:
        faces = []  # as _mark_split: only a rank changed while face down does that

    def uniform(rng: Random, n: int, held: int) -> tuple[list[int], list[int]]:
        picks = rng.sample(everything, min(n, len(pool)))
        return picks[:held], picks[held:]

    def split(rng: Random, n: int, held: int) -> tuple[list[int], list[int]]:
        down = rng.sample(faces, marked)  # every marked face-down card: those held, then those gone
        gone = set(down)
        rest = [j for j in everything if j not in gone]
        return down[:held], rng.sample(rest, min(n - held, len(rest)))
    return split if faces else uniform



class _FinalHands:
    """_final_hands's sampler: a final hand is one of the ways it can be made
    (_finals), drawn by their weight, with its unseen cards drawn (_drawer),
    DNA's copies of a face-down card all that one card, and Cerulean Bell's
    forced card, if not kept, a random one of the hand (as _bell_after_draw).
    A call draws one and says whether it makes the target."""

    def __init__(self, state: GameState, target: Target, discard: list[int], session: Session):
        self.session = session
        self.d = d = _discard(state, discard)
        self.hands = target.hands
        self.finals = _finals(state, discard, d, _bell_for(target, state), _same_for(target), card_key)
        self.weights = list(accumulate(f.weight for f in self.finals))
        self.known = [[card_key(c, f.rules) for c in f.known] for f in self.finals]
        self.tests: dict[tuple[Rules, bool, ClassKey | None], Callable[[Iterable[ClassKey | None]], bool]] = {}

    def test(self, rules: Rules, bell: bool,
             forced_key: ClassKey | None) -> Callable[[Iterable[ClassKey | None]], bool]:
        found = self.tests.get((rules, bell, forced_key))
        if found is None:
            found = self.tests[(rules, bell, forced_key)] = _target_test(self.hands, rules, bell, forced_key)
        return found

    @cached_property
    def sure(self) -> bool:
        """Whether every final hand makes the target whatever is drawn, without Cerulean Bell."""
        return all(not f.bell and self.test(f.rules, False, None)(known) for f, known in zip(self.finals, self.known))

    @cached_property
    def pools(self) -> dict[Rules, list[ClassKey | None]]:
        """The pool's class keys under each set of rules: only worked out to draw, which `sure` saves."""
        return {f.rules: _pool_keys(self.d.pool, f.rules, self.session) for f in self.finals}

    @cached_property
    def draw(self) -> Callable[[Random, int, int], tuple[list[int], list[int]]]:
        return _drawer(self.d.pool, self.d.marked)

    def __call__(self, rng: Random) -> bool:
        i = rng.choices(range(len(self.finals)), cum_weights=self.weights)[0] if len(self.finals) > 1 else 0
        f = self.finals[i]
        pool_keys = self.pools[f.rules]
        forced_key = None if f.forced is None else card_key(f.forced, f.rules)
        marked, others = self.draw(rng, f.unseen, f.marked_held)
        cards = self.known[i] + [pool_keys[j] for j in marked] + [pool_keys[j] for j in others]
        copy = marked if f.copy_marked else others
        if f.copies and copy:
            cards += [pool_keys[copy[0]]] * f.copies
        if f.bell and f.forced is None:
            if not cards:
                return False
            i = rng.randrange(len(cards))
            forced_key = cards[i]
            cards = cards[:i] + cards[i + 1:]
        return self.test(f.rules, f.bell, forced_key)(cards)



def estimate_cards(state: GameState, query: CardQuery, discard: list[int], cancel: Event | None = None,
                   seconds: float = 1.0, session: Session | None = None) -> Estimate | None:
    """evaluate_cards's odds estimated as estimate_discard's."""
    def odds(resolved: GameState, until: Event | None) -> Fraction:
        return _evaluate_cards(resolved, query, discard).probability

    return _estimate(state, discard, _rule_classes(state, discard, query.filter.test), odds, cancel, seconds,
                     _own(session))



def can_miss(state: GameState, target: Target, discard: list[int], rng: Random, tries: int = 200,
             cancel: Event | None = None, session: Session | None = None) -> bool:
    """Whether a draw that makes none of `target`'s hands was found, so that the
    odds after discarding `discard` are certainly below 1. False proves nothing.

    Much quicker than the odds, so a search that has already found odds of 1
    can rule a discard out. It tries final hands drawn as estimates draw them
    (_final_hands), each of which has some chance. It gives up (False) under
    Cerulean Bell, where each forced card's patterns would take a while to build.

    Where the hand's sorts tell about face-down cards, a uniform draw could be
    one they rule out, so each try first picks what those cards are: any branch
    of the posterior has some chance, and so has any draw from what's left.
    The branches are summed by what the target sees, so they're all it can
    tell apart.
    """
    if state.bell:
        return False
    session = _own(session)
    post = _posterior_for(state, discard, cancel, _rule_classes(state, discard, _counts_towards(target, state)),
                          session)
    if post is None:
        final = _final_hands(state, target, discard, session)
        return not final.sure and any(not final(rng) for _ in range(tries))
    branches: dict[int, _FinalHands] = {}
    for _ in range(tries):
        b = rng.randrange(len(post.branches))
        if b not in branches:
            branches[b] = _final_hands(_resolved(state, post.branches[b]), target, discard, session)
        if not branches[b].sure and not branches[b](rng):
            return True
    return False

