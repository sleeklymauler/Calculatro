"""The box over a hovered face-down card: what the hand's order leaves it able to be."""

import dataclasses
import itertools
import json
import random
from collections import Counter
from fractions import Fraction

import pytest

from calculatro.cards import parse_query
from calculatro.engine import evaluate_cards, evaluate_discard, posterior
from calculatro.hands import get_target
from calculatro.state import GameState
from game_order import game_order
from hand_driver import Game
from rounds import played, random_round, round_discards

pytest.importorskip("lupa")

SUITS = ("Spades", "Hearts", "Clubs", "Diamonds")

def ranks(cards):
    return sorted({c[0] for c in cards})


def test_a_face_down_card_between_a_9_and_a_7(game):
    down = game.card("8C", "back")
    game.deal([game.card("9H"), down, game.card("7D")], [game.card("8S"), game.card("2H"), game.card("KD")])
    # Neither a higher 7 nor a lower 9 is unseen, so it's one of the 8s.
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}
    game.hover(down)
    assert game.hint() == "8 @light|[ui_1 3,1]|[ui_1 2,1]"
    box = game.mod.hint_box
    assert game.rt.eval("rawequal")(box.config.major, down)
    assert box.config.instance_type == "POPUP" and box.config.can_collide is False
    game.hover(down, on=False)
    assert game.hint() is None and box.REMOVED


def test_the_example_rank_and_suit_sorts_narrow_it_together(game):
    # 6-8 from the rank sort; from the suit sort no spade, and no club is left in 6-8.
    down = game.card("7H", "back")
    high, low, two = game.card("9S"), game.card("5D"), game.card("2S")
    deck = [game.card(c) for c in ("6H", "8H", "6D", "7D", "8D", "KH")]
    game.deal([high, down, low, two], deck)
    assert ranks(game.possible(down)) == [6, 7, 8]
    game.sort("suit desc", [high, two, down, low])
    game.hover(down)
    assert game.hint() == "6-8 @light|[ui_1 0,1]|[ui_1 1,1]"


def test_a_hand_from_the_game(game):
    # The Wheel, 15 cards discarded face up. View Deck showed 28 unseen cards, with
    # 4H the only heart from 4 to 6: 5H and 6H had been discarded.
    down = game.card("4H", "back")
    seen = {c: game.card(c) for c in ("AC", "AD", "KD", "JS", "7H", "7C", "7D", "3S", "2S")}
    deck = ["AS", "QS", "9S", "7S", "5S", "AH", "KH", "KH", "JH", "9H",
            "KC", "QC", "JC", "TC", "9C", "8C", "6C", "5C", "4C", "2C", "QD", "JD", "9D", "8D", "6D", "5D", "2D"]
    discards = ["3D", "4D", "TD", "2H", "3H", "5H", "6H", "8H", "TH", "QH", "4S", "6S", "8S", "TS", "KS"]
    game.set_area("discard", [game.card(c, "back") for c in discards])
    by_rank = [*(seen[c] for c in ("AC", "AD", "KD", "JS", "7H", "7C", "7D")), down, seen["3S"], seen["2S"]]
    game.deal(by_rank, [game.card(c) for c in deck])
    assert len(game.mod.unseen_pool()) == 28
    # The 7S would sort left of the 7H, and any other 3 right of the 3S.
    assert ranks(game.possible(down)) == [4, 5, 6]
    game.sort("suit desc", [*(seen[c] for c in ("JS", "3S", "2S", "7H")), down,
                            *(seen[c] for c in ("AC", "7C", "AD", "KD", "7D"))])
    game.hover(down)
    assert game.hint() == "4 @light|[ui_1 0,1]"


@pytest.mark.parametrize("fifth", ["2S", "3S"])
def test_five_face_down_cards_from_the_game(game, fifth):
    # The Wheel, 5 of 10 cards face down, sorted by suit, then by rank. View Deck
    # showed these 27 unseen, the face-down ones included.
    pool = ["KS", "QS", "9S", "7S", "5S", "3S", "2S", "AH", "KH", "QH", "JH", "8H", "7H", "6H", "3H",
            "AC", "QC", "9C", "4C", "KD", "JD", "TD", "8D", "6D", "4D", "3D", "2D"]
    truth = ["KH", "QS", "QH", "4C", fifth]
    down = [game.card(c, "back") for c in truth]
    seen = {c: game.card(c) for c in ("AS", "AD", "JC", "TS", "4S")}
    by_rank = [seen["AS"], seen["AD"], *down[:3], seen["JC"], seen["TS"], seen["4S"], *down[3:]]
    game.deal(by_rank, [game.card(c) for c in pool if c not in truth])
    assert len(game.mod.unseen_pool()) == 27
    game.sort("suit desc", [seen["AS"], down[1], seen["TS"], seen["4S"], down[4], down[0], down[2],
                            seen["JC"], down[3], seen["AD"]])
    game.sort("desc", by_rank)
    # The same whichever spade the last one is: the hint can't tell them apart.
    assert [game.possible(d) for d in down] == [
        {(13, "Hearts")},
        {(12, "Spades")},  # the K♠ would leave nothing between it and the A♦ for the first
        {(12, "Hearts"), (11, "Hearts"), (12, "Clubs")},
        {(4, "Clubs")},  # left of the A♦ by suit, so no diamond; under the 4♠ by rank, so not the 9♣
        {(3, "Spades"), (2, "Spades")},  # a 3♥ first of the three by suit would put the K♥ under it
    ]
    game.hover(down[2])
    assert game.hint() == "J-Q @light|[ui_1 0,1]|Q @light|[ui_1 2,1]"


def test_a_whole_hand_face_down_sorted_both_ways_is_exact(game):
    # The House: eight face down from a full deck, sorted by rank, then by suit. Searching
    # card by card, the search used to stop at its limit here and list cards it hadn't ruled
    # out: every one of the 51 unseen for the A♠. These are the sets found with no limit.
    held = [(3, "Clubs"), (11, "Diamonds"), (13, "Spades"), (14, "Spades"),
            (12, "Hearts"), (10, "Hearts"), (5, "Diamonds"), (6, "Hearts")]
    named = [(str(c), c) for c in held]
    down = {name: game.make(c[0], c[1], "back") for name, c in named}
    deck = [game.make(r, s) for r in range(2, 15) for s in SUITS if (r, s) not in held]
    game.deal([down[n] for n in game_order(named, "desc")], deck)
    game.sort("suit desc", [down[n] for n in game_order(named, "suit desc")])

    def suits(*ranges):
        return {(r, s) for s, low, high in ranges for r in range(low, high + 1)}
    assert game.possible(down[str((3, "Clubs"))]) == suits(("Clubs", 2, 11), ("Hearts", 2, 10), ("Spades", 2, 9))
    assert game.possible(down[str((14, "Spades"))]) == suits(("Spades", 5, 14), ("Hearts", 6, 14), ("Clubs", 7, 14))


def test_a_card_discarded_face_down_is_still_possible(game):
    down, gone = game.card("8C", "back"), game.card("8S", "back")
    gone.ability.wheel_flipped = True
    game.set_area("discard", [gone, game.card("8H", "back")])  # 8H was discarded face up
    game.deal([game.card("9H"), down, game.card("7D")], [game.card("2C")])
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}


def test_one_discarded_face_down_after_the_sort_still_narrows(game):
    # Two face-down cards between the 10 and the 6: the left one is the higher.
    # Discarding the right one face down doesn't change that it was lower.
    high, left, right, low = game.card("TH"), game.card("9C", "back"), game.card("7C", "back"), game.card("6D")
    game.deal([high, left, right, low], [game.card("8C"), game.card("2H")])
    assert game.possible(left) == {(9, "Clubs"), (8, "Clubs")}
    right.ability.wheel_flipped = True
    game.set_area("hand", [high, left, low])
    game.set_area("discard", [right])
    game.frame()
    assert game.possible(left) == {(9, "Clubs"), (8, "Clubs")}
    assert game.latest("desc").cards[3].discarded
    # Leaving it out, as the hint used to, the left one could be the 7 as well.
    game.rt.eval("table.remove")(game.latest("desc").cards, 3)
    assert game.possible(left) == {(9, "Clubs"), (8, "Clubs"), (7, "Clubs")}


def test_it_needs_a_sort_of_the_hand(game):
    down = game.card("8C", "back")
    hand = [game.card("9H"), down, game.card("7D")]
    game.set_area("hand", hand)  # no sort the mod saw, as after loading a save
    game.frame(2)
    assert game.possible(down) is None
    game.hover(down)
    assert game.hint() is None
    game.sort("desc", hand)
    assert game.hint() == "8 @light|[ui_1 2,1]"


def test_the_left_of_two_face_down_cards_is_the_higher(game):
    # The plan's worked example.
    left, right = game.card("8C", "back"), game.card("7C", "back")
    game.deal([game.card("TS"), left, right, game.card("6H")], [game.card("9C")])
    assert ranks(game.possible(left)) == [8, 9]
    assert ranks(game.possible(right)) == [7, 8]


def test_lines_say_exactly_what_is_possible(game):
    lines = game.rt.execute("""return function(mod, list)
        return mod.hint_lines(list)
    end""")

    def show(*cards):
        values = game.rt.table_from([
            game.rt.table_from({"stone": True}) if c == "Stone" else game.rt.table_from({"rank_id": c[0], "suit": c[1]})
            for c in cards])
        return list(lines(game.mod, values).values())

    assert show(*[(r, s) for r in (6, 7, 8) for s in ("Hearts", "Diamonds")]) == ["6-8 ♥♦"]
    # Suits whose ranks differ get a line each: "2, K-A ♠♥" would claim a 2♥.
    assert show((14, "Spades"), (13, "Spades"), (2, "Spades"), (4, "Hearts")) == ["2, K-A ♠", "4 ♥"]
    assert show((10, "Hearts"), (11, "Hearts"), "Stone") == ["10-J ♥", "Stone"]


def test_a_stone_sorts_lowest(game):
    # After a face-up Stone it can only be a Stone; before one, it can still be one.
    wide, last = game.card("9D", "back"), game.card("Stone", "back")
    game.deal([game.card("TH"), wide, game.card("Stone"), last], [game.card("Stone"), game.card("2H")])
    assert game.possible(last) == {"Stone"}
    assert game.possible(wide) == {(2, "Hearts"), (9, "Diamonds"), "Stone"}


def test_copies_are_counted(game):
    # Two face-down cards can both be 9H only if there are two of them.
    left, right = game.card("9H", "back"), game.card("8C", "back")
    game.deal([game.card("TS"), left, right, game.card("7D")], [game.card("9H")])
    assert game.possible(right) == {(9, "Hearts"), (8, "Clubs")}
    assert game.possible(left) == {(9, "Hearts")}


def test_the_mark_leaves_face_cards(game):
    down = game.card("JC", "back")
    game.rt.execute("G.GAME.blind = {name = 'The Mark'}")
    game.deal([game.card("AS"), down, game.card("2H")], [game.card("9H"), game.card("KD")])
    assert game.possible(down) == {(11, "Clubs"), (13, "Diamonds")}
    # The challenge's random flips can flip any card.
    game.rt.execute("G.GAME.modifiers = {flipped_cards = 4}")
    assert (9, "Hearts") in game.possible(down)


def test_pareidolia_held_when_drawn_lets_it_be_any_card(game):
    # Every card is a face card then, so The Mark drew them all face down.
    down = game.card("9H", "back")
    game.rt.execute("G.GAME.blind = {name = 'The Mark'}; G.jokers.cards = {{config = {center_key = 'j_pareidolia'}}}")
    game.deal([game.card("AS"), down, game.card("2H")], [game.card("JC"), game.card("KD")])
    assert game.possible(down) == {(9, "Hearts"), (11, "Clubs"), (13, "Diamonds")}
    # Sold, it changes nothing about the cards already drawn: the box stays as it was.
    game.hover(down)
    shown = game.hint()
    game.rt.execute("G.jokers.cards = {}")
    game.frame()
    assert game.hint() == shown
    assert game.possible(down) == {(9, "Hearts"), (11, "Clubs"), (13, "Diamonds")}


def test_a_card_drawn_after_pareidolia_is_sold_is_a_face_card(game):
    first, second = game.card("9H", "back"), game.card("KD", "back")
    ace, two, jack, eight = game.card("AS"), game.card("2H"), game.card("JC"), game.card("8C")
    game.rt.execute("G.GAME.blind = {name = 'The Mark'}; G.jokers.cards = {{config = {center_key = 'j_pareidolia'}}}")
    game.deal([ace, first, two], [second, jack, eight])
    game.rt.execute("G.jokers.cards = {}")
    # The ace thrown away, the King drawn face down by The Mark: only it has to be a face card.
    discard_and_draw(game, [first, two], [ace], [second, first, two], [jack, eight])
    assert game.possible(second) == {(11, "Clubs"), (13, "Diamonds")}
    assert (8, "Clubs") in game.possible(first)


def test_pareidolia_gained_after_the_draw_changes_nothing(game):
    down = game.card("JC", "back")
    game.rt.execute("G.GAME.blind = {name = 'The Mark'}")
    game.deal([game.card("AS"), down, game.card("2H")], [game.card("9H"), game.card("KD")])
    game.rt.execute("G.jokers.cards = {{config = {center_key = 'j_pareidolia'}}}")
    game.frame()
    assert game.possible(down) == {(11, "Clubs"), (13, "Diamonds")}


def test_a_debuffed_pareidolia_when_drawn_does_nothing(game):
    down = game.card("JC", "back")
    game.rt.execute("G.GAME.blind = {name = 'The Mark'}; "
                    "G.jokers.cards = {{config = {center_key = 'j_pareidolia'}, debuff = true}}")
    game.deal([game.card("AS"), down, game.card("2H")], [game.card("9H"), game.card("KD")])
    assert game.possible(down) == {(11, "Clubs"), (13, "Diamonds")}


# --- What keeps an observation, and what ends it --------------------------------

@pytest.fixture
def sorted_hand(game):
    down = game.card("8C", "back")
    high, low = game.card("9H"), game.card("7D")
    game.deal([high, down, low], [game.card("8S")])
    assert game.possible(down) is not None
    return game, down, high, low


def test_a_drag_keeps_what_was_seen(sorted_hand):
    game, down, high, low = sorted_hand
    # Moved somewhere else, it's still the card that was between them.
    high.states.drag["is"] = True
    game.set_area("hand", [down, high, low])
    game.frame()
    high.states.drag["is"] = None
    game.frame(2)
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}


def test_a_sort_during_a_drag_proves_nothing(sorted_hand):
    game, down, high, low = sorted_hand
    # A face-down card drawn between one frame and the next, with a card being dragged.
    other, rest = game.card("2C", "back"), game.card("2S")
    game.set_area("deck", [other, rest])
    game.frame()
    high.states.drag["is"] = True
    game.set_area("deck", [rest])
    game.set_area("hand", [high, down, low, other])
    game.frame()
    high.states.drag["is"] = None
    game.frame(2)
    assert game.possible(other) is None


def test_a_drag_hides_the_box(sorted_hand):
    game, down, _, _ = sorted_hand
    game.hover(down)
    assert game.hint() is not None
    down.states.drag["is"] = True
    game.frame()
    assert game.hint() is None


def test_the_box_goes_with_the_panel(sorted_hand):
    game, down, _, _ = sorted_hand
    game.hover(down)
    game.mod.visible = False
    game.frame()
    assert game.hint() is None
    game.mod.visible = True
    game.rt.execute("G.STATE = G.STATES.HAND_PLAYED")
    game.frame()
    assert game.hint() is None


def test_a_consumable_ends_it_even_if_it_changed_nothing(sorted_hand):
    game, down, high, low = sorted_hand
    game.rt.execute("G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND; G.STATE = G.STATES.PLAY_TAROT")
    game.frame()
    game.rt.execute("G.TAROT_INTERRUPT = nil; G.STATE = G.STATES.SELECTING_HAND")
    game.frame(2)
    assert game.possible(down) is None  # leaving that state restores nothing
    game.sort("desc", [high, down, low])  # it takes a fresh sort
    assert game.possible(down) is not None


def test_cards_that_leave_the_hand(sorted_hand):
    game, down, high, low = sorted_hand
    other = game.card("6C", "back")
    game.deal([high, down, low, other], [game.card("8S")])
    # Played, it's turned up: its printed values are public.
    down.facing = "front"
    game.set_area("hand", [high, low, other])
    game.set_area("play", [down])
    game.frame()
    entries = game.latest("desc").cards
    assert [e.rank_id for e in entries.values() if e.rank_id] == [9, 8, 7]
    assert len(entries) == 4
    # Gone face down but not to the discard pile, it's left out. The sort then has
    # only face-up cards, all different, which says nothing, so it goes.
    game.set_area("hand", [high, low])
    game.frame()
    assert game.sorts() == []


def test_it_resets_for_a_new_round_and_a_new_hand_area(sorted_hand):
    game, down, high, low = sorted_hand
    game.rt.eval("G.GAME").round = 2
    game.frame()
    assert game.possible(down) is None
    game.sort("desc", [high, down, low])
    assert game.possible(down) is not None
    game.rt.execute("G.hand = {config = {sort = 'desc', card_limit = 8}, cards = {}}")
    game.frame()
    assert game.possible(down) is None


def test_a_rebuilt_box_of_buttons_is_no_sort(game):
    down = game.card("8C", "back")
    game.set_area("hand", [game.card("9H"), down, game.card("7D")])
    game.frame()
    game.rt.execute("G.buttons = make_buttons(); RANK_BUTTON.last_clicked = 5")
    game.frame(2)
    assert game.possible(down) is None
    game.rt.execute("G.buttons = nil")
    game.frame(2)
    assert game.possible(down) is None


def test_a_round_starting_with_no_buttons_still_counts_draws(game):
    # The game has no G.buttons while a blind's first hand is dealt.
    game.rt.execute("G.buttons = nil; G.GAME.round = 7")
    game.frame()
    down = game.card("8C", "back")
    game.deal([game.card("9H"), down, game.card("7D")], [game.card("8S")])
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}
    # The buttons appearing is no press, and pressing them after is.
    game.rt.execute("G.buttons = make_buttons()")
    game.frame(2)
    assert game.latest("suit desc") is None
    game.rt.eval("press")(game.rt.eval("SUIT_BUTTON"))
    game.rt.eval("G.hand.config").sort = "suit desc"
    game.frame(2)
    assert game.latest("suit desc") is not None


def test_unsupported_modes_and_out_of_order_cards_record_nothing(game):
    down = game.card("8C", "back")
    high, low = game.card("9H"), game.card("7D")
    game.deal([low, down, high], [], mode="asc")
    assert game.possible(down) is None
    game.sort("desc", [low, down, high])  # face-up cards out of order: not the game's sort
    assert game.possible(down) is None


# --- Earlier sorts -----------------------------------------------------------------

def discard_and_draw(game, kept, thrown, drawn, deck, wheel_flipped=()):
    """Discards `thrown` (face down: those in `wheel_flipped`), then draws into `drawn`, the
    hand as the draw sorts it, leaving `deck`."""
    for card in wheel_flipped:
        card.ability.wheel_flipped = True
    game.set_area("hand", kept)
    game.set_area("discard", list(game.rt.eval("G.discard").cards.values()) + list(thrown))
    game.frame()
    game.set_area("deck", deck)
    game.set_area("hand", drawn)
    game.frame(2)


def test_a_draw_keeps_what_the_earlier_sort_said(game):
    # The plan's example: between a 9♣ and a 7♣, then between a K and a 4 after both are
    # discarded and the hand drawn up again.
    nine, down, seven = game.card("9C"), game.card("8C", "back"), game.card("7C")
    king, four = game.card("KD"), game.card("4H")
    rest = [game.card(c) for c in ("8S", "6H", "TD")]
    game.deal([nine, down, seven], [king, four, *rest])
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}
    discard_and_draw(game, [down], [nine, seven], [king, down, four], rest)
    assert [o.mode for o in game.sorts()] == ["desc", "desc"]
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades")}
    # With only the latest sort, as before, it could be anything between the K and the 4.
    game.rt.eval("table.remove")(game.mod.observations, 1)
    assert game.possible(down) == {(8, "Clubs"), (8, "Spades"), (6, "Hearts"), (10, "Diamonds")}
    # They all go with the round.
    game.rt.eval("G.GAME").round = 2
    game.frame()
    assert game.sorts() == []


def test_a_sort_that_adds_nothing_changes_nothing(sorted_hand):
    game, down, high, low = sorted_hand
    before = game.mod.observed
    game.sort("desc", [high, down, low])
    assert len(game.sorts()) == 1 and game.mod.observed == before
    # One that says all an earlier one of its mode did, and more, takes its place.
    other = game.card("2C")
    game.deal([high, down, low, other], [game.card("8S")])
    assert len(game.sorts()) == 1 and len(game.latest("desc").cards) == 4
    # Another mode's is kept beside it, even if it has the same cards.
    game.sort("suit desc", [high, other, low, down])
    assert [o.mode for o in game.sorts()] == ["desc", "suit desc"]


def test_a_sort_of_face_up_cards_is_kept_only_for_copies(game):
    # With nothing face down left, a sort says nothing, unless two of its cards are alike
    # in print: then it orders them, and a face-down card elsewhere may tie with them.
    first, second, down = game.card("9H"), game.card("9H"), game.card("2C", "back")
    game.deal([first, second, down], [game.card("8S")])
    down.facing = "front"  # played
    game.set_area("hand", [first, second])
    game.set_area("play", [down])
    game.frame()
    assert len(game.sorts()) == 1
    assert not any(e.latent for e in game.latest("desc").cards.values())


def test_a_card_seen_differently_drops_only_the_sorts_that_saw_it(game):
    high, left, right, low = game.card("9H"), game.card("8C", "back"), game.card("7C", "back"), game.card("6S")
    two, rest = game.card("2C"), game.card("KD")
    game.deal([high, left, right, low], [two, rest])
    discard_and_draw(game, [high, left, low], [right], [high, left, low, two], [rest], wheel_flipped=[right])
    assert len(game.sorts()) == 2
    first = game.sorts()[0]
    # Nothing the mod knows of changes a face-up card without a consumable, but if
    # something did, the sort that saw the 2♣ as it was goes; the first never had it.
    two.base.id = 3
    game.sort("desc", [high, left, low, two])
    assert len(game.sorts()) == 2
    assert game.rt.eval("rawequal")(game.sorts()[0], first)
    assert [e.rank_id for e in game.sorts()[1].cards.values()] == [9, None, 6, 3]


# --- Against dealing every way ---------------------------------------------------

def deals_matching(face_up, pool, names, views):
    """Every way of dealing the pool to the face-down cards that shows these views."""
    known = [(c, c) for c in face_up]
    for assignment in itertools.permutations(pool, len(names)):
        cards = list(zip(names, assignment)) + known
        if all(game_order(cards, mode) == view for mode, view in views.items()):
            yield dict(zip(names, assignment))


@pytest.mark.parametrize("seed", range(30))
def test_against_dealing_every_way(game, seed):
    rng = random.Random(seed)
    # A narrow deck, so that hands say something and few deals fit them.
    deck = [(r, s) for r in range(5, 12) for s in SUITS]
    cards = rng.sample(deck, rng.randint(7, 11))
    face_up, pool = cards[:3], cards[3:]
    latent = rng.sample(pool, rng.randint(1, 3))
    names = [f"L{i}" for i in range(len(latent))]
    truth = list(zip(names, latent)) + [(c, c) for c in face_up]
    modes = rng.choice([["desc"], ["suit desc"], ["desc", "suit desc"], ["suit desc", "desc"]])
    views = {m: game_order(truth, m) for m in modes}

    objects = {c: game.make(c[0], c[1]) for c in face_up}
    objects.update({n: game.make(v[0], v[1], "back") for n, v in zip(names, latent)})
    rest = [game.make(c[0], c[1]) for c in pool if c not in latent]
    game.deal([objects[e] for e in views[modes[0]]], rest, mode=modes[0])
    if len(modes) > 1:
        game.sort(modes[1], [objects[e] for e in views[modes[1]]])
    hand = views[modes[-1]]
    # Every other seed, one is then discarded face down: what the sorts said stays true.
    gone = names[-1] if seed % 2 and len(names) > 1 else None
    if gone:
        objects[gone].ability.wheel_flipped = True
        hand = [n for n in hand if n != gone]
        game.set_area("hand", [objects[n] for n in hand])
        game.set_area("discard", [objects[gone]])
        game.frame()

    deals = list(deals_matching(face_up, pool, names, views))
    for name in names:
        if name != gone:
            assert game.possible(objects[name]) == {deal[name] for deal in deals}, (name, modes)

    # The odds read the same sorts from the snapshot: with no two cards alike, every
    # deal showing them is as likely, so each face-down card is each card as often
    # as the deals say, and can be what the hint lists and nothing else.
    state = GameState.from_json(json.loads(game.rt.eval("require('calculatro.json')").encode(game.mod.snapshot())))
    post = posterior(state)
    for name in names:
        chance: Counter = Counter()
        for b in post.branches:
            c = b.discarded[0] if name == gone else dict(b.cards)[hand.index(name)]
            chance[(c.rank_id, c.suit)] += b.weight / post.total
        assert chance == {v: Fraction(sum(d[name] == v for d in deals), len(deals)) for v in chance}
        if name != gone:
            assert set(chance) == game.possible(objects[name])


def replay(game, rnd, drawn, truth):
    """Plays a round of test_posterior's out in the mocked game, as the mod would see
    it: returns each slot's Lua card."""
    facings = rnd.facings

    def make(c, down):
        if c.is_stone:
            return game.make(5, "Clubs", "back" if down else "front", "m_stone")
        return game.make(c.rank_id, c.suit, "back" if down else "front", c.enhancement)
    objects = {f"s{i}": make(rnd.deck[n], facings[i]) for i, n in enumerate(drawn)}
    # The deck, top first: the slots in the order drawn, then the cards never drawn.
    deck = [objects[f"s{i}"] for i in range(len(drawn))]
    deck += [make(c, False) for n, c in rnd.deck.items() if n not in drawn]
    discard, held = [], 0
    for event in truth.events:
        kind = event[0]
        if kind == "deal":
            hand = [objects[s] for s in event[2]]
            deck = deck[len(hand):]
            game.deal(hand, deck, mode=event[1])
            held = len(hand)
        elif kind == "sort":
            game.sort(event[1], [objects[s] for s in event[2]])
        elif kind == "throw":
            thrown = [objects[s] for s in event[2]]
            for s, card in zip(event[2], thrown):
                if facings[int(s[1:])]:
                    if event[1] == "play":
                        card.facing = "front"
                    else:
                        card.ability.wheel_flipped = True
            game.set_area("hand", [objects[s] for s in event[3]])
            if event[1] == "play":
                game.set_area("play", thrown)
                game.frame()
                game.set_area("play", [])
            discard += thrown
            game.set_area("discard", discard)
            game.frame()
            held = len(event[3])
        else:  # a draw, from the top of the deck, sorted in the current mode
            assert game.rt.eval("G.hand.config").sort == event[1]
            deck = deck[len(event[2]) - held:]
            game.set_area("deck", deck)
            game.set_area("hand", [objects[s] for s in event[2]])
            game.frame(2)
            held = len(event[2])
    return objects


@pytest.mark.parametrize("seed", range(12))
def test_rounds_pruned_as_kept_and_the_hint_agrees(game, seed):
    # Every sort of a round, as the mod keeps them (less those the others imply, or that
    # say nothing), give the same odds as keeping every one; and the hint lists exactly
    # what each face-down card can be by them, as no two cards are alike here.
    rng = random.Random(1100 + seed)
    rnd, drawn = random_round(rng, plays=seed % 2 == 1)
    truth = played(rnd, drawn, seed)
    objects = replay(game, rnd, drawn, truth)
    case = rnd.case(truth)
    every = case.state()
    state = GameState.from_json(json.loads(game.rt.eval("require('calculatro.json')").encode(game.mod.snapshot())))
    assert [c.hidden for c in state.hand] == [c.hidden for c in every.hand]
    assert Counter((c.rank_id, c.suit, c.enhancement) for c in state.unseen) == Counter(
        (c.rank_id, c.suit, c.enhancement) for c in every.unseen)  # the mock names ranks by number
    assert len(state.observations) <= len(every.observations)
    kept = dataclasses.replace(state, observations=every.observations, observed=every.observed,
                               hand=[dataclasses.replace(c, latent=e.latent) for c, e in zip(state.hand, every.hand)])
    for discard in round_discards(rng, case):
        for target in (get_target("Pair"), get_target("Two Pair")):
            assert (evaluate_discard(state, target, discard).probability
                    == evaluate_discard(kept, target, discard).probability), (target.name, discard)
        for query in (parse_query("9"), parse_query("at least 2 hearts")):
            assert (evaluate_cards(state, query, discard).probability
                    == evaluate_cards(kept, query, discard).probability), (query.text, discard)
    post = posterior(state)
    for i, name in enumerate(case.hand):
        if state.hand[i].latent is not None:
            can_be = {(c.rank_id, c.suit) for b in post.branches for j, c in b.cards if j == i}
            assert game.possible(objects[name]) == can_be, name


def test_some_rounds_are_pruned(lua):
    # So the test above compares something.
    pruned = 0
    for seed in range(12):
        game = Game(lua)
        rnd, drawn = random_round(random.Random(1100 + seed), plays=seed % 2 == 1)
        truth = played(rnd, drawn, seed)
        replay(game, rnd, drawn, truth)
        pruned += len(game.sorts()) < len(truth.sorts)
        game.rt.eval("G.GAME").round = 100 + seed
        game.frame()
    assert pruned >= 3


def test_which_card_is_face_down_changes_nothing_shown(lua):
    # The same public view, with the face-down card and a deck card swapped.
    def shown(down_text, deck_text):
        game = Game(lua)
        down = game.card(down_text, "back")
        game.deal([game.card("9H"), down, game.card("7D")], [game.card(deck_text), game.card("KC")])
        game.hover(down)
        text = game.hint()
        game.hover(down, on=False)
        return text

    assert shown("8C", "8S") == shown("8S", "8C") == "8 @light|[ui_1 3,1]|[ui_1 2,1]"
