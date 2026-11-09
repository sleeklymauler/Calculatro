"""The observations the snapshot sends: what the hand's order showed of its face-down cards.

The mod keeps them in `observe.lua` (tested with the hint in `test_hint.py`); here is
how they go out, and how `GameState` parses them. See docs/plans/face-down-windows.md.
"""

from __future__ import annotations

import pytest

from calculatro.state import SCHEMA, GameState, MalformedState, Observation, ObservedCard
from test_hint import Game, game  # noqa: F401  (fixture: a hand moved frame by frame)
from test_state import lua, new_lua, snapshot_json  # noqa: F401  (fixture: the mod against a mocked game)

pytest.importorskip("lupa")


def exported(game: Game) -> list:  # noqa: F811
    return snapshot_json((game.rt, game.mod))["observations"]


def parsed(game: Game) -> GameState:  # noqa: F811
    return GameState.from_json(snapshot_json((game.rt, game.mod)))


@pytest.fixture
def two_sorts(game):  # noqa: F811
    """Two face-down cards between a 9♥ and a 6♠ by rank, sorted by rank and then by suit."""
    high, left, right, low = game.card("9H"), game.card("8C", "back"), game.card("7C", "back"), game.card("6S")
    game.deal([high, left, right, low], [game.card("8S"), game.card("KD")])
    game.sort("suit desc", [low, high, left, right])
    return game, high, left, right, low


# --- What the mod sends -------------------------------------------------------------

def test_both_sorts_with_shared_ids(two_sorts):
    g, *_ = two_sorts
    # Ids are numbered by first appearance, the rank sort first.
    assert exported(g) == [
        {"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Hearts"}, {"id": 1, "hand": 2},
                                   {"id": 2, "hand": 3}, {"id": 3, "rank_id": 6, "suit": "Spades"}]},
        {"mode": "suit desc", "cards": [{"id": 3, "rank_id": 6, "suit": "Spades"},
                                        {"id": 0, "rank_id": 9, "suit": "Hearts"},
                                        {"id": 1, "hand": 2}, {"id": 2, "hand": 3}]},
    ]
    state = parsed(g)
    assert [c.short() for c in state.hand] == ["6♠", "9♥", "??", "??"]
    assert state.observations == (Observation("desc", (0, 1, 2, 3)), Observation("suit desc", (3, 0, 1, 2)))
    assert state.observed == (ObservedCard(rank_id=9, suit="Hearts"), ObservedCard(hand=2), ObservedCard(hand=3),
                              ObservedCard(rank_id=6, suit="Spades"))


def test_a_stone(game):  # noqa: F811
    down = game.card("8C", "back")
    game.deal([game.card("9H"), down, game.card("Stone")], [game.card("8S")])
    assert exported(game) == [{"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Hearts"},
                                                         {"id": 1, "hand": 1}, {"id": 2, "stone": True}]}]
    assert parsed(game).observed[2] == ObservedCard(stone=True)


def test_nothing_without_the_setting(two_sorts):
    g, *_ = two_sorts
    g.mod.settings.face_down = False
    raw = snapshot_json((g.rt, g.mod))
    assert raw["observations"] == []  # an array, even empty
    assert parsed(g).observations == () and parsed(g).observed == ()
    # The mod watches the hand either way, so turning it on sends what it saw at once.
    g.mod.settings.face_down = True
    assert len(exported(g)) == 2


@pytest.mark.parametrize("flips, disabled, sent", [(False, False, True), (True, False, False), (True, True, True)])
def test_the_mark_with_random_flips_sends_nothing(two_sorts, flips, disabled, sent):
    # The odds' Mark model is face cards only, which random flips make untrue.
    g, *_ = two_sorts
    g.rt.execute(f"G.GAME.blind = {{name = 'The Mark', disabled = {str(disabled).lower()}}}"
                 f"; G.GAME.modifiers = {{flipped_cards = {'4' if flips else 'nil'}}}")
    assert len(exported(g)) == (2 if sent else 0)


def test_nothing_before_a_sort(game):  # noqa: F811
    game.set_area("hand", [game.card("9H"), game.card("8C", "back")])  # as after loading a save
    game.frame(2)
    assert exported(game) == []


def test_a_drag_keeps_the_ids_and_moves_the_indices(two_sorts):
    g, high, left, right, low = two_sorts
    before = exported(g)
    left.states.drag["is"] = True
    g.set_area("hand", [left, low, high, right])
    g.frame()
    left.states.drag["is"] = None
    g.frame(2)
    after = exported(g)
    assert after[0]["cards"][1] == {"id": 1, "hand": 0}
    assert after[0]["cards"][2] == {"id": 2, "hand": 3}
    # Only the hand indices changed.
    def strip(observations):
        return [[{k: v for k, v in c.items() if k != "hand"} for c in o["cards"]] for o in observations]
    assert strip(after) == strip(before)
    assert parsed(g).observed[1] == ObservedCard(hand=0)


def test_a_played_card_is_shown_and_a_discarded_one_stays(two_sorts):
    g, high, left, right, low = two_sorts
    # Played, the left one is turned up: what it is is public now.
    left.facing = "front"
    g.set_area("hand", [low, high, right])
    g.set_area("play", [left])
    g.frame()
    desc, suit = exported(g)
    assert desc["cards"] == [{"id": 0, "rank_id": 9, "suit": "Hearts"}, {"id": 1, "rank_id": 8, "suit": "Clubs"},
                             {"id": 2, "hand": 2}, {"id": 3, "rank_id": 6, "suit": "Spades"}]
    assert suit["cards"][2] == {"id": 1, "rank_id": 8, "suit": "Clubs"}
    # Discarded face down, the right one stays where it was seen, without saying
    # which of the discard pile's face-down cards it is.
    right.ability.wheel_flipped = True
    g.set_area("play", [])
    g.set_area("hand", [low, high])
    g.set_area("discard", [left, right])
    left.facing = "back"  # the discard pile turns every card over
    g.frame()
    assert exported(g) == [
        {"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Hearts"}, {"id": 1, "rank_id": 8, "suit": "Clubs"},
                                   {"id": 2, "discarded": True}, {"id": 3, "rank_id": 6, "suit": "Spades"}]},
        {"mode": "suit desc", "cards": [{"id": 3, "rank_id": 6, "suit": "Spades"},
                                        {"id": 0, "rank_id": 9, "suit": "Hearts"},
                                        {"id": 1, "rank_id": 8, "suit": "Clubs"}, {"id": 2, "discarded": True}]},
    ]
    state = parsed(g)
    assert state.observed[2] == ObservedCard(discarded=True) and state.observed[2].latent
    assert all(c.latent is None for c in state.hand)
    # A sort of the rest, both face up, says nothing: the earlier sorts stay as they were.
    before = exported(g)
    g.sort("suit desc", [low, high])
    assert exported(g) == before


def test_earlier_sorts_go_out_in_the_order_recorded(game):  # noqa: F811
    # Sorted by rank, by suit, then a discard and a draw sorted by suit again.
    nine, left, right, six = game.card("9H"), game.card("8C", "back"), game.card("7C", "back"), game.card("6S")
    two, rest = game.card("2C"), game.card("KD")
    game.deal([nine, left, right, six], [two, rest])
    game.sort("suit desc", [six, nine, left, right])
    right.ability.wheel_flipped = True
    game.set_area("hand", [six, nine, left])
    game.set_area("discard", [right])
    game.frame()
    game.set_area("deck", [rest])
    game.set_area("hand", [six, nine, left, two])
    game.frame(2)
    assert exported(game) == [
        {"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Hearts"}, {"id": 1, "hand": 2},
                                   {"id": 2, "discarded": True}, {"id": 3, "rank_id": 6, "suit": "Spades"}]},
        {"mode": "suit desc", "cards": [{"id": 3, "rank_id": 6, "suit": "Spades"},
                                        {"id": 0, "rank_id": 9, "suit": "Hearts"},
                                        {"id": 1, "hand": 2}, {"id": 2, "discarded": True}]},
        {"mode": "suit desc", "cards": [{"id": 3, "rank_id": 6, "suit": "Spades"},
                                        {"id": 0, "rank_id": 9, "suit": "Hearts"},
                                        {"id": 1, "hand": 2}, {"id": 4, "rank_id": 2, "suit": "Clubs"}]},
    ]
    state = parsed(game)
    assert [o.mode for o in state.observations] == ["desc", "suit desc", "suit desc"]
    assert state.observations[2] == Observation("suit desc", (3, 0, 1, 4))


LEFT_IN_HAND = [{"id": 0, "rank_id": 9, "suit": "Hearts"}, {"id": 1, "hand": 2},
                {"id": 2, "rank_id": 6, "suit": "Spades"}]


def test_a_face_down_card_gone_anywhere_else_is_left_out(two_sorts):
    # Not in hand, not turned up, and not marked face down in the discard pile (so
    # turned up there): nothing the mod knows of does that, so it's dropped.
    g, high, left, right, low = two_sorts
    g.set_area("hand", [low, high, left])
    g.set_area("discard", [right])
    g.frame()
    desc, suit = exported(g)
    assert desc["cards"] == LEFT_IN_HAND  # and the ids close up
    assert len(suit["cards"]) == 3
    parsed(g)


def test_a_discarded_card_back_in_hand_or_turned_up(two_sorts):
    g, high, left, right, low = two_sorts
    right.ability.wheel_flipped = True
    g.set_area("hand", [low, high, left])
    g.set_area("discard", [right])
    g.frame()
    assert exported(g)[0]["cards"][2] == {"id": 2, "discarded": True}
    # Back in hand still face down (no game card does it), it's in hand again.
    g.set_area("discard", [])
    g.set_area("hand", [low, high, left, right])
    g.frame()
    assert exported(g)[0]["cards"][2] == {"id": 2, "hand": 3}
    # No longer marked face down in the discard pile, it's left out.
    g.set_area("hand", [low, high, left])
    g.set_area("discard", [right])
    g.frame()
    assert exported(g)[0]["cards"][2] == {"id": 2, "discarded": True}
    right.ability.wheel_flipped = None
    g.frame()
    assert exported(g)[0]["cards"] == LEFT_IN_HAND


def test_a_consumable_clears_them(two_sorts):
    g, *_ = two_sorts
    g.rt.execute("G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND; G.STATE = G.STATES.PLAY_TAROT")
    g.frame()
    assert exported(g) == []


def test_a_card_seen_differently_drops_the_other_sort(two_sorts):
    # A card is one id in both sorts, so it must be seen alike in both. Nothing the
    # mod knows of changes a face-up card in hand without a consumable, which clears
    # both, but if something did, the older sort goes.
    g, high, left, right, low = two_sorts
    low.base.id = 5
    g.sort("desc", [high, left, right, low])
    assert [o["mode"] for o in exported(g)] == ["desc"]
    assert exported(g)[0]["cards"][3] == {"id": 3, "rank_id": 5, "suit": "Spades"}


@pytest.mark.parametrize("runtime", ["lupa", "lupa.luajit21"])
def test_which_cards_are_face_down_changes_nothing_sent(runtime, tmp_path):
    # Two games the player can't tell apart: the same face-up cards, the same
    # face-down positions, the same events, and the same unseen multiset. Only
    # which unseen cards are face down in hand, then discarded face down, differs.
    # Every snapshot, frame by frame, must be the same.
    def play(down: tuple[str, str], deck: tuple[str, str]) -> list:
        g = Game(new_lua(runtime, tmp_path / f"{down[0]}-{runtime}"))
        snapshots = []
        frame = g.frame

        def recording_frame(n=1):
            for _ in range(n):
                frame()
                snapshots.append(snapshot_json((g.rt, g.mod)))
        g.frame = recording_frame

        king, nine, seven, two, three = (g.card(c) for c in ("KS", "9S", "7H", "2C", "3D"))
        a, b = g.card(down[0], "back"), g.card(down[1], "back")
        # The face-down ones are a spade between the K♠ and the 9♠, and a heart under
        # the 7♥ but over the 2♣, in both sorts.
        g.deal([king, a, nine, seven, b, two], [g.card(deck[0]), g.card(deck[1]), three])
        g.sort("suit desc", [king, a, nine, seven, b, two])
        a.states.drag["is"] = True  # dragged to the end
        g.set_area("hand", [king, nine, seven, b, two, a])
        g.frame()
        a.states.drag["is"] = None
        g.frame(2)
        g.rt.execute("G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND; G.STATE = G.STATES.PLAY_TAROT")  # changes nothing
        g.frame()
        g.rt.execute("G.TAROT_INTERRUPT = nil; G.STATE = G.STATES.SELECTING_HAND")
        g.frame(2)
        g.sort("desc", [king, a, nine, seven, b, two])
        g.mod.settings.face_down = False
        g.frame()
        g.mod.settings.face_down = True
        g.sort("suit desc", [king, a, nine, seven, b, two])
        b.ability.wheel_flipped = True  # discarded face down
        g.set_area("hand", [king, a, nine, seven, two])
        g.set_area("discard", [b])
        g.frame()
        # The 3♦ drawn: the draw sorts by suit. The earlier sorts stay, with b.
        spade, heart = g.card(deck[0]), g.card(deck[1], "back")
        g.set_area("deck", [spade, heart])
        g.set_area("hand", [king, a, nine, seven, two, three])
        g.frame(2)
        # The 9♠ and the 2♣ discarded face up, and the other heart drawn face down,
        # where b was in both games; then sorted by rank, the same in both too.
        g.set_area("hand", [king, a, seven, three])
        g.set_area("discard", [b, nine, two])
        g.frame()
        g.set_area("deck", [spade])
        g.set_area("hand", [king, a, seven, heart, three])
        g.frame(2)
        g.sort("desc", [king, a, seven, heart, three])
        g.sort("desc", [king, a, seven, heart, three])  # adds nothing
        g.rt.eval("G.GAME").round = 2
        g.frame()
        return snapshots

    first, second = play(("QS", "6H"), ("JS", "5H")), play(("JS", "5H"), ("QS", "6H"))
    assert first == second
    assert {len(s["observations"]) for s in first} == {0, 1, 2, 3, 4, 5}  # it had something to send
    assert sum(any(c.get("discarded") for o in s["observations"] for c in o["cards"]) for s in first) >= 3
    assert sum(len(s["observations"]) == 5 for s in first) >= 3


# --- What the app parses ------------------------------------------------------------

HAND = [{"rank_id": 9, "suit": "Hearts"}, {"hidden": True}, {"hidden": True}]


def snapshot(*observations, hand=HAND) -> dict:
    return {"schema": SCHEMA, "in_run": True, "hand": hand, "observations": list(observations)}


def nine(card_id=0):
    return {"id": card_id, "rank_id": 9, "suit": "Hearts"}


def test_ids_are_numbered_by_first_appearance_in_the_order_sent():
    # Whatever the ids sent, equal evidence parses equal. The sorts keep the order
    # the mod recorded them in.
    state = GameState.from_json(snapshot(
        {"mode": "suit desc", "cards": [{"id": 7, "hand": 2}, nine(5)]},
        {"mode": "desc", "cards": [nine(5), {"id": 3, "hand": 1}, {"id": 7, "hand": 2}]},
    ))
    assert state.observations == (Observation("suit desc", (0, 1)), Observation("desc", (1, 2, 0)))
    assert state.observed == (ObservedCard(hand=2), ObservedCard(rank_id=9, suit="Hearts"), ObservedCard(hand=1))
    assert [c.latent for c in state.observed] == [True, False, True]


def test_a_mode_may_repeat():
    # Earlier sorts are kept: a draw re-sorts the hand, but what they said is still true.
    state = GameState.from_json({**snapshot(
        {"mode": "desc", "cards": [nine(0), {"id": 1, "discarded": True}, {"id": 2, "hand": 1}]},
        {"mode": "suit desc", "cards": [nine(0), {"id": 2, "hand": 1}]},
        {"mode": "desc", "cards": [nine(0), {"id": 2, "hand": 1}, {"id": 3, "hand": 2}]}),
        "discard": [{"hidden": True}]})
    assert state.observations == (Observation("desc", (0, 1, 2)), Observation("suit desc", (0, 2)),
                                  Observation("desc", (0, 2, 3)))
    assert [c.latent for c in state.hand] == [None, 2, 3]
    # An id is still the same card everywhere.
    with pytest.raises(MalformedState):
        GameState.from_json(snapshot({"mode": "desc", "cards": [nine(0)]},
                                     {"mode": "desc", "cards": [{"id": 0, "rank_id": 8, "suit": "Hearts"}]}))


def test_a_card_discarded_face_down():
    state = GameState.from_json({**snapshot(
        {"mode": "desc", "cards": [nine(4), {"id": 6, "discarded": True}, {"id": 3, "hand": 1}]},
        {"mode": "suit desc", "cards": [{"id": 6, "discarded": True}, nine(4)]}),
        "discard": [{"hidden": True}, {"rank_id": 2, "suit": "Clubs"}]})
    assert state.observations == (Observation("desc", (0, 1, 2)), Observation("suit desc", (1, 0)))
    assert state.observed[1] == ObservedCard(discarded=True)
    assert [c.latent for c in state.observed] == [False, True, True]
    assert [c.latent for c in state.hand] == [None, 2, None]


@pytest.mark.parametrize("raw", [None, [], {}])
def test_no_observations(raw):
    state = GameState.from_json({**snapshot(), "observations": raw})
    assert state.observations == () and state.observed == ()


def test_impossible_evidence_is_not_malformed():
    # Face-up cards out of order, or a card no deal fits: for the odds to set
    # aside, not a reason to skip the snapshot.
    state = GameState.from_json(snapshot(
        {"mode": "desc", "cards": [{"id": 0, "rank_id": 2, "suit": "Clubs"}, {"id": 1, "hand": 1}, nine(2)]}))
    assert len(state.observations) == 1


@pytest.mark.parametrize("observations", [
    "desc",
    [["desc"]],
    [{"mode": "asc", "cards": []}],
    [{"cards": []}],
    [{"mode": "desc", "cards": {"id": 0}}],
    [{"mode": "desc", "cards": [None]}],
    # Not exactly one kind.
    [{"mode": "desc", "cards": [{"id": 0}]}],
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Hearts", "hand": 1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "stone": True, "hand": 1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "stone": True, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0, "stone": False}]}],
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": 9}]}],
    [{"mode": "desc", "cards": [{"id": 0, "suit": "Hearts"}]}],
    # Ids.
    [{"mode": "desc", "cards": [{"rank_id": 9, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": "0", "rank_id": 9, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": True, "rank_id": 9, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0.5, "rank_id": 9, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [nine(0), nine(0)]}],
    [{"mode": "desc", "cards": [nine(0)]}, {"mode": "suit desc", "cards": [{"id": 0, "rank_id": 8, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": 1}]}, {"mode": "suit desc", "cards": [{"id": 0, "hand": 2}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": 1}]}, {"mode": "suit desc", "cards": [{"id": 0, "stone": True}]}],
    # Printed values.
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": 1, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": 15, "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": "9", "suit": "Hearts"}]}],
    [{"mode": "desc", "cards": [{"id": 0, "rank_id": 9, "suit": "Stars"}]}],
    # Hand indices.
    [{"mode": "desc", "cards": [{"id": 0, "hand": 3}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": -1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": 0}]}],  # a face-up card
    [{"mode": "desc", "cards": [{"id": 0, "hand": 1.0}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": 1}, {"id": 1, "hand": 1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "hand": 1}]}, {"mode": "suit desc", "cards": [{"id": 1, "hand": 1}]}],
    # Discarded face down: at most as many as the discard pile has face down (one here).
    [{"mode": "desc", "cards": [{"id": 0, "discarded": False}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": 1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": True, "hand": 1}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": True, "stone": True}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": True}, {"id": 1, "discarded": True}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": True}]},
     {"mode": "suit desc", "cards": [{"id": 1, "discarded": True}]}],
    [{"mode": "desc", "cards": [{"id": 0, "discarded": True}]}, {"mode": "suit desc", "cards": [{"id": 0, "hand": 1}]}],
])
def test_malformed_observations_raise(observations):
    discard = [{"hidden": True}, {"rank_id": 2, "suit": "Clubs"}]
    with pytest.raises(MalformedState):
        GameState.from_json({**snapshot(), "discard": discard, "observations": observations})


def test_discarded_needs_a_face_down_card_in_the_discard_pile():
    observations = [{"mode": "desc", "cards": [{"id": 0, "discarded": True}]}]
    with pytest.raises(MalformedState):
        GameState.from_json({**snapshot(), "discard": [{"rank_id": 2, "suit": "Clubs"}], "observations": observations})
    assert GameState.from_json({**snapshot(), "discard": [{"hidden": True}], "observations": observations}).observed
