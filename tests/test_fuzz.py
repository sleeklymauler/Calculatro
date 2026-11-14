"""Random states against dealing every way (`state_oracle`), and the best-discard search against trying every
discard (`search_oracle`): bosses, DNA, rule jokers, enhancements, face-down cards with and without the hand's
sorts, small and empty decks, and the selection limit, in any mix.

Each seed makes one state, the same on any machine. A few seeds run with every `pytest`; the rest are the `fuzz`
tier: `pytest -m fuzz`, with `--fuzz-seeds N` and `--fuzz-base B` for seeds B to B+N-1 (tests/conftest.py). A
failure prints the state as the mod would export it and what its face-down cards are; `--fuzz-base` with the seed
and `--fuzz-seeds 1` runs it again.
"""

import json
import random
from collections import Counter
from math import comb, perm

import pytest

from calculatro.cards import parse_query
from calculatro.engine import evaluate_cards, evaluate_discard
from calculatro.hands import combine, get_target
from calculatro.state import SUITS, Card, GameState
from game_order import nominal
from poker import card
from search_oracle import check as check_search
from state_oracle import Case, as_json, check, crimson_rules, face_for_the_mark, printed

# Estimates stop at a deadline: by a clock that moves with the work done, so they are the same on any machine.
pytestmark = pytest.mark.usefixtures("fake_clock")

BOSSES = [None, None, None, "The Hook", "The Mark", "The House", "The Wheel", "The Serpent", "Cerulean Bell",
          "Crimson Heart", "The Psychic"]
FACE_DOWN = {"The Mark", "The House", "The Wheel"}  # the bosses that leave cards face down
RULE_JOKERS = ["j_four_fingers", "j_shortcut", "j_smeared", "j_pareidolia"]
QUERIES = ["queen", "2+ hearts", "at most 1 face", "exactly 2 lucky", "between 1 and 3 not hearts", "no spades",
           "9", "between 1 and 2 even"]
# Final hands the oracle may play out for one discard: each is a few hundred evaluate_poker_hand calls at most.
BUDGET = 20000


def random_card(rng: random.Random, ranks: list[int], suits: list[str]) -> Card:
    r, s, roll = rng.choice(ranks), rng.choice(suits), rng.random()
    if roll < 0.08:
        return card(r, s, "m_wild", debuff=rng.random() < 0.3)
    if roll < 0.13:
        return card(r, s, "m_stone")
    if roll < 0.2:
        return card(r, s, "m_lucky")
    return card(r, s, debuff=rng.random() < 0.04)


def random_jokers(rng: random.Random, blind: str | None) -> list[dict]:
    if blind == "Crimson Heart":
        # Rule jokers for it to debuff, and others; one already debuffed, and now and then a run-out Perishable.
        jokers = [{"key": k} for k in rng.sample(RULE_JOKERS, rng.randint(1, 3))]
        jokers += [{"key": "j_joker"} for _ in range(rng.randint(0, 2))]
        rng.shuffle(jokers)
        rng.choice(jokers)["debuff"] = True
        if len(jokers) > 1 and rng.random() < 0.3:
            jokers[-1] |= {"debuff": True, "perished": True}
        return jokers
    jokers = [{"key": k} for k in RULE_JOKERS if rng.random() < 0.15]
    if jokers and rng.random() < 0.1:
        jokers[0]["debuff"] = True
    return jokers


def random_case(rng: random.Random) -> Case:
    blind = rng.choice(BOSSES)
    disabled = blind is not None and rng.random() < 0.1
    jokers = random_jokers(rng, blind)
    pareidolia = any(j["key"] == "j_pareidolia" and not j.get("debuff") for j in jokers)
    # A few ranks close together, so hands are made, or now and then every rank.
    if rng.random() < 0.25:
        ranks = list(range(2, 15))
    else:
        low = rng.randint(2, 10)
        ranks = list(range(low, min(14, low + rng.randint(4, 6)) + 1))
    suits = rng.sample(SUITS, rng.choice([1, 2, 2, 2, 3, 4]))

    def new() -> Card:
        return random_card(rng, ranks, suits)

    cards = {f"C{i}": new() for i in range(rng.choice([1, 2, 3, 4, 4, 5, 5, 5, 6, 6, 6, 7, 7]))}
    names = list(cards)
    # A deck the draw takes all of gives odds of 0 or 1, so those are only now and then.
    deck = [new() for _ in range(rng.choice([0, 1, 2, 3] + [5, 6, 7, 8, 9] * 3))]
    down: set[str] = set()
    marked: set[str] = set()
    discarded: list[Card] = []
    if blind in FACE_DOWN and not disabled:
        if blind == "The Mark":
            # Drawn face down: face cards without Pareidolia, and marked; every card with it, unmarked.
            down = {n for n in names if pareidolia or face_for_the_mark(cards[n])}
            marked = set() if pareidolia else set(down)
        else:
            down = {n for n in names if rng.random() < 0.4}
        if rng.random() < 0.25:
            c = new()
            if blind != "The Mark" or pareidolia or face_for_the_mark(c):
                discarded.append(c)
                if blind == "The Mark" and not pareidolia:
                    marked.add("discarded 0")
    views: dict[str, list[str]] = {}
    if down and len(down) <= 3 and rng.random() < 0.5:
        # One hidden order of the cards alike in print, the same for every sort; the hand is in the last one's.
        tiebreak = {n: rng.randrange(100) for n in names}
        for mode in rng.choice([["desc"], ["suit desc"], ["desc", "suit desc"]]):
            views[mode] = sorted(names, key=lambda n: nominal(printed(cards[n]), mode, tiebreak[n]), reverse=True)
        names = views[mode]
    else:
        rng.shuffle(names)
    forced = rng.choice(names) if blind == "Cerulean Bell" and not disabled else None
    kw = {"blind": blind, "blind_disabled": disabled, "jokers": jokers,
          "hand_size": max(1, len(names) + rng.choice([0, 0, 0, 0, 0, -1, 1, 2])),
          "discards_left": rng.choice([0, 0, 1, 2]), "hands_left": rng.choice([1, 2, 2, 3]),
          "hands_played": rng.choice([0, 0, 1]), "dna_copies": rng.choice([0, 0, 0, 0, 1, 1, 2]),
          "select_limit": rng.choice([5, 5, 5, 5, 0, 1, 2, 3])}
    return Case(hand=names, cards=cards, down=down, views=views, deck=deck, discarded=discarded, forced=forced,
                kw=kw, marked=marked)


def oracle_cost(case: Case, state: GameState, discard: list[int]) -> int:
    """How many final hands `state_oracle.oracle` plays out for `discard`, at most."""
    slots = sum(n in case.down for n in case.hand) + len(case.gone) + len(case.discarded)
    unseen = len(case.unseen)
    played = bool(discard) and state.discards_left <= 0
    kept = len(case.hand) - len(discard)
    hooked = min(2, kept) if state.hook and played else 0
    deck = unseen - slots
    draws = 0 if not discard else 3 if state.serpent else state.hand_size - kept + hooked
    rules = len(crimson_rules(state)) if state.crimson and played else 1
    return perm(unseen, slots) * comb(kept, hooked) * comb(deck, max(0, min(draws, deck))) * rules


def random_discards(rng: random.Random, case: Case, state: GameState) -> list[list[int]]:
    n, forced = len(case.hand), state.forced
    picks = [[]]
    for _ in range(3):
        size = rng.randint(1, min(5, n))
        if forced is None:
            picks.append(sorted(rng.sample(range(n), size)))
        else:
            others = [i for i in range(n) if i != forced]
            picks.append(sorted([forced, *rng.sample(others, min(size - 1, len(others)))]))
    picks = [list(d) for d in dict.fromkeys(map(tuple, picks))]
    return [d for d in picks if oracle_cost(case, state, d) <= BUDGET]


# The rarer hands are mostly out of reach in a deck this small, so they come up less often.
TARGET_WEIGHTS = {"Pair": 3, "Two Pair": 3, "Three of a Kind": 3, "Straight": 3, "Flush": 3, "Full House": 2,
                  "Four of a Kind": 2, "Straight Flush": 1, "Five of a Kind": 1, "Flush House": 1, "Flush Five": 1}


def random_targets(rng: random.Random):
    def pick() -> str:
        return rng.choices(list(TARGET_WEIGHTS), list(TARGET_WEIGHTS.values()))[0]

    targets = [get_target(name) for name in dict.fromkeys([pick(), pick()])]
    if rng.random() < 0.25:
        targets.append(combine([get_target(pick()), get_target(pick())]))
    return targets


def described(case: Case) -> str:
    """The state as the mod would export it, and what its face-down cards are, to reproduce a failure."""
    truth = {n: as_json(case.cards[n]) for n in case.hand if n in case.down}
    truth |= {f"discarded {i}": as_json(c) for i, c in enumerate(case.discarded)}
    return (f"snapshot: {json.dumps(case.snapshot())}\nface down: {json.dumps(truth)}\n"
            f"marked: {sorted(case.marked)}")


def test_odds_and_search(fuzz_seed):
    rng = random.Random(f"fuzz {fuzz_seed}")
    case = random_case(rng)
    state = case.state()
    discards = random_discards(rng, case, state)
    targets = random_targets(rng)
    queries = [parse_query(q) for q in rng.sample(QUERIES, 2)]
    try:
        check(case, discards, targets, queries)
        if len(case.hand) <= 6:
            check_search(state, targets[0])
            check_search(state, queries[0])
    except AssertionError as e:
        raise AssertionError(f"seed {fuzz_seed}: {e}\ndiscards: {discards}\n{described(case)}") from e


def test_the_cases_reach_what_they_should():
    """The generator makes every kind of state it's meant to, and odds other than 0 and 1, which would pass
    whatever the engine did."""
    seen: Counter = Counter()
    for seed in range(300):
        rng = random.Random(f"fuzz {seed}")
        case = random_case(rng)
        state = case.state()
        seen[state.blind] += 1
        seen["sorted"] += bool(case.views)
        seen["face down"] += bool(case.down)
        seen["discarded face down"] += bool(case.discarded)
        seen["marked"] += bool(case.marked)
        seen["empty deck"] += state.deck_size == 0
        seen["select limit under 2"] += state.select_limit < 2
        seen["dna played"] += state.dna_copies > 0 and state.discards_left == 0 and not state.hands_played
        seen["rule jokers"] += state.rules != type(state.rules)()
        seen["disabled"] += state.blind_disabled
        discards = random_discards(rng, case, state)
        seen["discards"] += len(discards) - 1
        if seed < 100:
            target, query = random_targets(rng)[0], parse_query(rng.choice(QUERIES))
            # Keeping the hand draws nothing: 0 or 1 but for face-down cards.
            odds = [evaluate_discard(state, target, d).probability for d in discards[1:]]
            odds += [evaluate_cards(state, query, d).probability for d in discards[1:]]
            seen["odds between"] += sum(0 < p < 1 for p in odds)
            seen["odds"] += len(odds)
    for boss in BOSSES:
        assert seen[boss] >= 10, boss
    for what in ("sorted", "discarded face down", "marked", "empty deck", "select limit under 2", "dna played",
                 "rule jokers", "disabled"):
        assert seen[what] >= 8, (what, seen)
    assert seen["discards"] >= 600, seen  # most discards fit the oracle's budget
    # A small deck makes many targets out of reach: about a quarter of the odds are neither 0 nor 1.
    assert seen["odds between"] >= seen["odds"] // 5, seen
