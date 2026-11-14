"""The sampled model of a final hand (engine._final_hands, which estimates draw from) against the exact odds
(evaluate_discard), under every rule the two each have a version of: The Hook, The Mark, Cerulean Bell, Crimson
Heart, The Serpent and DNA, alone and together (docs/probability-model.md#approximations).

A rule added to one model and not the other would make the estimates disagree with the exact odds. Here each
model's share of final hands that make a target agrees with the other's, within 4 standard errors. Seeded, so
stable. Each case also checks that its rule changes the exact odds by more than that, so a sampled model that
left the rule out would fail.

The states have no sorts of the hand (`observations`): estimates from deals of the face-down cards they tell
about are checked in test_posterior.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from calculatro import engine
from calculatro.engine import evaluate_discard, flipped_by_mark
from calculatro.hands import Target, combine, get_target
from calculatro.state import SUITS, Card, GameState, Joker
from poker import card

TARGETS = [get_target(name) for name in ("Pair", "Two Pair", "Three of a Kind", "Straight", "Flush",
                                         "Full House")]
TARGETS.append(combine([get_target("Three of a Kind"), get_target("Flush")]))
SAMPLES = 4000
STATES = 3  # random states per case


def dense(rng: random.Random) -> Card:
    """A card from 8 to Ace, so most targets are within reach; some wild or stone."""
    r, s, roll = rng.randint(8, 14), rng.choice(SUITS), rng.random()
    if roll < 0.08:
        return card(r, s, "m_wild")
    if roll < 0.12:
        return card(r, s, "m_stone")
    return card(r, s)


def face(rng: random.Random) -> Card:
    """What The Mark draws face down without Pareidolia (engine.flipped_by_mark)."""
    return card(rng.randint(11, 13), rng.choice(SUITS))


@dataclass
class Situation:
    state: GameState
    discards: list[list[int]]
    without: GameState | None  # the state with the case's rule off, or None if there's no one rule


def deal(rng: random.Random, *, blind: str | None = None, down: int = 0, down_discarded: int = 0,
         marked: bool = False, forced: bool = False, **kw) -> GameState:
    """A hand of 6 to 8 cards, `down` of them face down (`marked` by The Mark), with `down_discarded` cards
    discarded face down, and 6 to 12 more cards left to draw. With `forced`, Cerulean Bell forces a face-up one."""
    size = rng.randint(6, 8)
    hand = [dense(rng) for _ in range(size - down)]
    if forced:
        hand[0] = replace(hand[0], forced=True)
    hand += [Card(hidden=True, marked=marked) for _ in range(down)]
    rng.shuffle(hand)
    really = [face(rng) if marked else dense(rng) for _ in range(down + down_discarded)]
    deck = [dense(rng) for _ in range(rng.randint(6, 12))]
    discard = [dense(rng) for _ in range(rng.randint(0, 3))] + [Card(hidden=True, marked=marked)] * down_discarded
    return GameState(**{"in_run": True, "hand_size": size, "hands_left": 2, "discards_left": 1, "blind": blind,
                        "hand": hand, "unseen": deck + really, "discard": discard, **kw})


def some_discards(rng: random.Random, state: GameState, lone: bool = False) -> list[list[int]]:
    """Keeping the hand, and a few discards of 1 to 3 cards. With `lone`, a lone face-up card and a lone face-down
    one (what DNA copies). Under Cerulean Bell each discard has the forced card, as in the game."""
    n, forced = len(state.hand), state.forced
    found: list[tuple[int, ...]] = [()]
    if lone:
        up = [i for i, c in enumerate(state.hand) if not c.hidden]
        down = [i for i, c in enumerate(state.hand) if c.hidden]
        found += [(rng.choice(up),)] + ([(rng.choice(down),)] if down else [])
    while len(found) < (6 if lone else 5):
        picked = set(rng.sample(range(n), rng.randint(1, 3)))
        if forced is not None:
            picked.add(forced)
        if tuple(sorted(picked)) not in found:
            found.append(tuple(sorted(picked)))
    return [list(d) for d in found]


def dna(state: GameState, rng: random.Random) -> GameState:
    """`state` as the round's first hand with no discards left, DNA making 1 to 3 copies of a lone card played."""
    return replace(state, discards_left=0, hands_played=0, dna_copies=rng.randint(1, 3))


def unmarked(state: GameState) -> GameState:
    return replace(state, hand=[replace(c, marked=False) for c in state.hand],
                   discard=[replace(c, marked=False) for c in state.discard])


def the_hook(rng):
    state = deal(rng, blind="The Hook", down=rng.randint(0, 2), discards_left=0)
    return Situation(state, some_discards(rng, state), replace(state, blind_disabled=True))


def the_hook_with_dna(rng):
    state = dna(deal(rng, blind="The Hook", down=rng.randint(0, 1)), rng)
    return Situation(state, some_discards(rng, state, lone=True), replace(state, dna_copies=0))


def the_mark(rng):
    state = deal(rng, blind="The Mark", down=rng.randint(1, 3), down_discarded=rng.randint(0, 2), marked=True,
                 discards_left=rng.randint(0, 1))
    return Situation(state, some_discards(rng, state), unmarked(state))


def the_mark_with_dna(rng):
    state = dna(deal(rng, blind="The Mark", down=rng.randint(1, 2), down_discarded=rng.randint(0, 1), marked=True),
                rng)
    return Situation(state, some_discards(rng, state, lone=True), replace(state, dna_copies=0))


def the_mark_after_a_rank_changed(rng):
    """Fewer face cards unseen than marked cards, which only a rank changed while face down does (Strength): both
    models then deal the marked cards from the whole pool."""
    state = deal(rng, blind="The Mark", down=2, down_discarded=rng.randint(0, 1), marked=True,
                 discards_left=rng.randint(0, 1))
    faces = [i for i, c in enumerate(state.unseen) if flipped_by_mark(c)]
    unseen = [replace(c, rank="Ace", rank_id=14) if i in faces[1:] else c for i, c in enumerate(state.unseen)]
    return Situation(replace(state, unseen=unseen), some_discards(rng, state), None)


def cerulean_bell(rng):
    state = deal(rng, blind="Cerulean Bell", forced=True, discards_left=rng.randint(0, 1))
    return Situation(state, some_discards(rng, state), replace(state, blind_disabled=True))


def cerulean_bell_with_dna(rng):
    state = dna(deal(rng, blind="Cerulean Bell", forced=True), rng)
    return Situation(state, [[], [state.forced], *some_discards(rng, state)[1:3]], replace(state, dna_copies=0))


def crimson_heart(rng):
    keys = ["j_four_fingers", "j_shortcut", "j_smeared", "j_joker"]
    jokers = [Joker(key, debuff=rng.random() < 0.3) for key in rng.sample(keys, rng.randint(2, 4))]
    state = deal(rng, blind="Crimson Heart", discards_left=0, jokers=jokers)
    return Situation(state, some_discards(rng, state), replace(state, blind_disabled=True))


def the_serpent(rng):
    state = deal(rng, blind="The Serpent", down=rng.randint(0, 2), down_discarded=rng.randint(0, 1))
    return Situation(state, some_discards(rng, state), replace(state, blind_disabled=True))


def dna_with_face_down_cards(rng):
    state = dna(deal(rng, down=rng.randint(1, 2), down_discarded=rng.randint(0, 1)), rng)
    return Situation(state, some_discards(rng, state, lone=True), replace(state, dna_copies=0))


def face_down_cards(rng):
    state = deal(rng, down=rng.randint(1, 3), down_discarded=rng.randint(0, 2), discards_left=rng.randint(0, 1))
    return Situation(state, some_discards(rng, state), None)


CASES: dict[str, Callable[[random.Random], Situation]] = {f.__name__: f for f in (
    the_hook, the_hook_with_dna, the_mark, the_mark_with_dna, the_mark_after_a_rank_changed, cerulean_bell,
    cerulean_bell_with_dna, crimson_heart, the_serpent, dna_with_face_down_cards, face_down_cards)}


def share(state: GameState, target: Target, discard: list[int]) -> float:
    final = engine._final_hands(state, target, discard)
    rng = random.Random(1)
    return sum(final(rng) for _ in range(SAMPLES)) / SAMPLES


def margin(p: float) -> float:
    """4 standard errors of a share of SAMPLES; none at 0 or 1, where no final hand drawn may differ."""
    return 4 * (p * (1 - p) / SAMPLES) ** 0.5 + 1e-9


@pytest.mark.parametrize("case", CASES)
def test_final_hands_drawn_agree_with_the_exact_odds(case):
    rng = random.Random(case)
    biggest = 0.0  # the most the case's rule changes the exact odds by, in margins
    ruled = False
    for _ in range(STATES):
        situation = CASES[case](rng)
        ruled = situation.without is not None
        for discard in situation.discards:
            for target in TARGETS:
                exact = float(evaluate_discard(situation.state, target, discard).probability)
                got = share(situation.state, target, discard)
                assert abs(got - exact) <= margin(exact), (target.name, discard, got, exact, situation.state)
                if situation.without is not None:
                    other = float(evaluate_discard(situation.without, target, discard).probability)
                    biggest = max(biggest, abs(other - exact) / max(margin(exact), margin(other)))
    if ruled:
        assert biggest > 3, "the rule hardly changes the odds here, so leaving it out wouldn't show"
